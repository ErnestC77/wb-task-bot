from datetime import date, timedelta
from unittest.mock import AsyncMock

from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from sqlalchemy import select

from bot.database.models import (
    ArticleAction, ArticleCategory, CheckStatus, DecisionType, ProblemType,
)
from bot.database.repositories.article_check_repository import ArticleCheckRepository
from bot.keyboards.article_check_keyboards import ActCb
from bot.services.article_check_service import ArticleCheckService
from bot.services.setting_service import SettingService
from bot.states.article_check_states import ArticleActionStates
from tests.test_article_check_service import seed


def _state_for(user_id: int) -> FSMContext:
    storage = MemoryStorage()
    key = StorageKey(bot_id=1, chat_id=user_id, user_id=user_id)
    return FSMContext(storage=storage, key=key)


def _denied(sender: AsyncMock, answer_attr: str = "answer") -> None:
    ans = getattr(sender, answer_attr)
    ans.assert_awaited()
    args, kwargs = ans.call_args
    text = args[0] if args else kwargs.get("text", "")
    if "show_alert" in kwargs:
        assert kwargs.get("show_alert") is True
    assert any(w in text.lower() for w in ("прав", "недоступ"))


async def test_deactivated_problem_type_hidden(session):        # тест 18
    inst, valya, _ = await seed(session)
    prob = (await session.execute(select(ProblemType))).scalar_one()
    prob.is_active = False
    await session.commit()
    from bot.handlers.article_check import active_problem_types
    items = await active_problem_types(session)
    assert prob.id not in [p.id for p in items]                  # нет в новых кнопках


async def test_historic_action_keeps_old_name(session):         # тест 19
    inst, valya, _ = await seed(session, n_articles=1)
    svc = ArticleCheckService(session)
    s = await svc.start_check(inst, valya)
    item = (await ArticleCheckRepository(session).get_items_for_batch(s.id, 1, 15))[0]
    await svc.mark(item.id, 1, CheckStatus.ACTION_REQUIRED, valya)
    from bot.database.models import ArticleCategory
    cat = (await session.execute(select(ArticleCategory))).scalar_one()
    prob = (await session.execute(select(ProblemType))).scalar_one()
    dec = (await session.execute(select(DecisionType))).scalar_one()
    action = await svc.create_action(item.id, valya, cat.id, prob.id, dec.id,
                                     None, date(2026, 7, 13))
    prob.name = "ПЕРЕИМЕНОВАНО"
    await session.commit()
    assert action.problem_name_snapshot == "высокий CPL"         # история не изменилась


async def test_default_next_check_from_setting(session):        # тест 17
    inst, valya, _ = await seed(session)
    await SettingService(session).set("article_check.default_next_check_days", 5, valya.id)
    from bot.handlers.article_check import default_next_check_date
    got = await default_next_check_date(session, problem_type=None, decision_type=None)
    assert got == date.today() + timedelta(days=5)


async def _start_marked_item(session, n_articles=1):
    """Общая подготовка: сессия проверки, один артикул отмечен ACTION_REQUIRED."""
    inst, valya, owner = await seed(session, n_articles=n_articles)
    svc = ArticleCheckService(session)
    s = await svc.start_check(inst, valya)
    item = (await ArticleCheckRepository(session).get_items_for_batch(s.id, 1, 15))[0]
    await svc.mark(item.id, item.version, CheckStatus.ACTION_REQUIRED, valya)
    await session.commit()
    return inst, valya, owner, s, item


async def test_full_action_fsm_happy_path(session):
    inst, valya, _, s, item = await _start_marked_item(session)
    cat = (await session.execute(select(ArticleCategory))).scalar_one()
    prob = (await session.execute(select(ProblemType))).scalar_one()
    dec = (await session.execute(select(DecisionType))).scalar_one()

    from bot.handlers.article_check import (
        handle_action_category, handle_action_comment, handle_action_decision,
        handle_action_next_check, handle_action_problem, start_action_fsm,
    )

    state = _state_for(valya.telegram_id)
    callback = AsyncMock()
    callback.from_user.id = valya.telegram_id
    await start_action_fsm(callback, item.id, session, state)
    assert await state.get_state() == ArticleActionStates.category.state

    await handle_action_category(callback, ActCb(a="cat", id=cat.id, it=item.id), session, state)
    assert await state.get_state() == ArticleActionStates.problem.state

    await handle_action_problem(callback, ActCb(a="prob", id=prob.id, it=item.id), session, state)
    assert await state.get_state() == ArticleActionStates.decision.state

    await handle_action_decision(callback, ActCb(a="dec", id=dec.id, it=item.id), session, state)
    assert await state.get_state() == ArticleActionStates.comment.state

    message = AsyncMock()
    message.from_user.id = valya.telegram_id
    message.text = "снизить ставку рекламы вручную"
    await handle_action_comment(message, session, state)
    assert await state.get_state() == ArticleActionStates.next_check.state

    message2 = AsyncMock()
    message2.from_user.id = valya.telegram_id
    message2.text = "13.07.2026"
    await handle_action_next_check(message2, session, state)
    assert await state.get_state() is None                       # FSM очищен

    action = (await session.execute(select(ArticleAction))).scalar_one()
    assert action.category_name_snapshot == cat.name
    assert action.problem_name_snapshot == prob.name
    assert action.decision_name_snapshot == dec.name
    assert action.comment == "снизить ставку рекламы вручную"
    assert action.next_check_date == date(2026, 7, 13)
    message2.answer.assert_awaited()                              # возврат к пачке


async def test_action_fsm_skip_optional_comment(session):
    """require_comment=False у проблемы/решения — /skip допустим, comment остаётся None."""
    inst, valya, _, s, item = await _start_marked_item(session)
    cat = (await session.execute(select(ArticleCategory))).scalar_one()
    prob = (await session.execute(select(ProblemType))).scalar_one()
    dec = (await session.execute(select(DecisionType))).scalar_one()
    assert prob.require_comment is False and dec.require_comment is False

    from bot.handlers.article_check import (
        handle_action_category, handle_action_comment, handle_action_decision,
        handle_action_next_check, handle_action_problem, start_action_fsm,
    )

    state = _state_for(valya.telegram_id)
    callback = AsyncMock()
    callback.from_user.id = valya.telegram_id
    await start_action_fsm(callback, item.id, session, state)
    await handle_action_category(callback, ActCb(a="cat", id=cat.id, it=item.id), session, state)
    await handle_action_problem(callback, ActCb(a="prob", id=prob.id, it=item.id), session, state)
    await handle_action_decision(callback, ActCb(a="dec", id=dec.id, it=item.id), session, state)

    message = AsyncMock()
    message.from_user.id = valya.telegram_id
    message.text = "/skip"
    await handle_action_comment(message, session, state)
    assert await state.get_state() == ArticleActionStates.next_check.state

    message2 = AsyncMock()
    message2.from_user.id = valya.telegram_id
    message2.text = "13.07.2026"
    await handle_action_next_check(message2, session, state)

    action = (await session.execute(select(ArticleAction))).scalar_one()
    assert action.comment is None


async def test_action_comment_rejects_skip_when_required(session):
    inst, valya, _, s, item = await _start_marked_item(session)
    cat = (await session.execute(select(ArticleCategory))).scalar_one()
    prob = (await session.execute(select(ProblemType))).scalar_one()
    prob.require_comment = True
    dec = (await session.execute(select(DecisionType))).scalar_one()
    await session.commit()

    from bot.handlers.article_check import (
        handle_action_category, handle_action_comment, handle_action_decision,
        handle_action_problem, start_action_fsm,
    )

    state = _state_for(valya.telegram_id)
    callback = AsyncMock()
    callback.from_user.id = valya.telegram_id
    await start_action_fsm(callback, item.id, session, state)
    await handle_action_category(callback, ActCb(a="cat", id=cat.id, it=item.id), session, state)
    await handle_action_problem(callback, ActCb(a="prob", id=prob.id, it=item.id), session, state)
    await handle_action_decision(callback, ActCb(a="dec", id=dec.id, it=item.id), session, state)

    message = AsyncMock()
    message.from_user.id = valya.telegram_id
    message.text = "/skip"
    await handle_action_comment(message, session, state)
    assert await state.get_state() == ArticleActionStates.comment.state  # остались на шаге
    message.answer.assert_awaited()
    text = message.answer.call_args.args[0]
    assert "обязателен" in text.lower()


async def test_action_comment_rejects_too_long(session):
    inst, valya, _, s, item = await _start_marked_item(session)
    await SettingService(session).set("general.max_comment_length", 10, valya.id)
    cat = (await session.execute(select(ArticleCategory))).scalar_one()
    prob = (await session.execute(select(ProblemType))).scalar_one()
    dec = (await session.execute(select(DecisionType))).scalar_one()

    from bot.handlers.article_check import (
        handle_action_category, handle_action_comment, handle_action_decision,
        handle_action_problem, start_action_fsm,
    )

    state = _state_for(valya.telegram_id)
    callback = AsyncMock()
    callback.from_user.id = valya.telegram_id
    await start_action_fsm(callback, item.id, session, state)
    await handle_action_category(callback, ActCb(a="cat", id=cat.id, it=item.id), session, state)
    await handle_action_problem(callback, ActCb(a="prob", id=prob.id, it=item.id), session, state)
    await handle_action_decision(callback, ActCb(a="dec", id=dec.id, it=item.id), session, state)

    message = AsyncMock()
    message.from_user.id = valya.telegram_id
    message.text = "это точно больше десяти символов"
    await handle_action_comment(message, session, state)
    assert await state.get_state() == ArticleActionStates.comment.state
    text = message.answer.call_args.args[0]
    assert "длин" in text.lower()


async def test_action_next_check_rejects_out_of_bounds_date(session):
    inst, valya, _, s, item = await _start_marked_item(session)
    cat = (await session.execute(select(ArticleCategory))).scalar_one()
    prob = (await session.execute(select(ProblemType))).scalar_one()
    dec = (await session.execute(select(DecisionType))).scalar_one()

    from bot.handlers.article_check import (
        handle_action_category, handle_action_comment, handle_action_decision,
        handle_action_next_check, handle_action_problem, start_action_fsm,
    )

    state = _state_for(valya.telegram_id)
    callback = AsyncMock()
    callback.from_user.id = valya.telegram_id
    await start_action_fsm(callback, item.id, session, state)
    await handle_action_category(callback, ActCb(a="cat", id=cat.id, it=item.id), session, state)
    await handle_action_problem(callback, ActCb(a="prob", id=prob.id, it=item.id), session, state)
    await handle_action_decision(callback, ActCb(a="dec", id=dec.id, it=item.id), session, state)

    message = AsyncMock()
    message.from_user.id = valya.telegram_id
    message.text = "снизить"
    await handle_action_comment(message, session, state)

    message2 = AsyncMock()
    message2.from_user.id = valya.telegram_id
    message2.text = "01.01.2020"                          # далеко за пределами границ
    await handle_action_next_check(message2, session, state)
    assert await state.get_state() == ArticleActionStates.next_check.state  # не продвинулись
    action_count = len((await session.execute(select(ArticleAction))).scalars().all())
    assert action_count == 0


# --- Проверки авторизации (по образцу находок Task 17) ---

async def test_action_category_denies_unregistered_user(session):
    inst, valya, _, s, item = await _start_marked_item(session)
    cat = (await session.execute(select(ArticleCategory))).scalar_one()
    from bot.handlers.article_check import handle_action_category, start_action_fsm

    state = _state_for(valya.telegram_id)
    callback = AsyncMock()
    callback.from_user.id = valya.telegram_id
    await start_action_fsm(callback, item.id, session, state)

    intruder_cb = AsyncMock()
    intruder_cb.from_user.id = 999999999                 # не зарегистрирован
    await handle_action_category(intruder_cb, ActCb(a="cat", id=cat.id, it=item.id),
                                 session, state)
    _denied(intruder_cb)
    intruder_cb.message.edit_text.assert_not_awaited()
    assert await state.get_state() == ArticleActionStates.category.state  # не продвинулось


async def test_action_category_denies_foreign_registered_user(session):
    inst, valya, owner, s, item = await _start_marked_item(session)
    cat = (await session.execute(select(ArticleCategory))).scalar_one()
    from bot.handlers.article_check import handle_action_category, start_action_fsm

    state = _state_for(valya.telegram_id)
    callback = AsyncMock()
    callback.from_user.id = valya.telegram_id
    await start_action_fsm(callback, item.id, session, state)

    intruder_cb = AsyncMock()
    intruder_cb.from_user.id = owner.telegram_id          # зарегистрирован, но не ответственный
    await handle_action_category(intruder_cb, ActCb(a="cat", id=cat.id, it=item.id),
                                 session, state)
    _denied(intruder_cb)
    intruder_cb.message.edit_text.assert_not_awaited()
    assert await state.get_state() == ArticleActionStates.category.state


async def test_action_category_rejects_item_id_spoofed_in_callback(session):
    """Callback с it, не совпадающим с item_id в FSM-данных — подделка, отказ."""
    inst, valya, _, s, item = await _start_marked_item(session, n_articles=2)
    cat = (await session.execute(select(ArticleCategory))).scalar_one()
    from bot.handlers.article_check import handle_action_category, start_action_fsm

    state = _state_for(valya.telegram_id)
    callback = AsyncMock()
    callback.from_user.id = valya.telegram_id
    await start_action_fsm(callback, item.id, session, state)
    callback.message.edit_text.reset_mock()

    other_item_id = item.id + 1
    await handle_action_category(
        callback, ActCb(a="cat", id=cat.id, it=other_item_id), session, state)
    callback.answer.assert_awaited()
    args, kwargs = callback.answer.call_args
    assert kwargs.get("show_alert") is True
    callback.message.edit_text.assert_not_awaited()
    assert await state.get_state() == ArticleActionStates.category.state


async def test_action_problem_denies_foreign_registered_user(session):
    inst, valya, owner, s, item = await _start_marked_item(session)
    cat = (await session.execute(select(ArticleCategory))).scalar_one()
    prob = (await session.execute(select(ProblemType))).scalar_one()
    from bot.handlers.article_check import (
        handle_action_category, handle_action_problem, start_action_fsm,
    )

    state = _state_for(valya.telegram_id)
    callback = AsyncMock()
    callback.from_user.id = valya.telegram_id
    await start_action_fsm(callback, item.id, session, state)
    await handle_action_category(callback, ActCb(a="cat", id=cat.id, it=item.id), session, state)

    intruder_cb = AsyncMock()
    intruder_cb.from_user.id = owner.telegram_id
    await handle_action_problem(intruder_cb, ActCb(a="prob", id=prob.id, it=item.id),
                                session, state)
    _denied(intruder_cb)
    intruder_cb.message.edit_text.assert_not_awaited()
    assert await state.get_state() == ArticleActionStates.problem.state


async def test_action_decision_denies_foreign_registered_user(session):
    inst, valya, owner, s, item = await _start_marked_item(session)
    cat = (await session.execute(select(ArticleCategory))).scalar_one()
    prob = (await session.execute(select(ProblemType))).scalar_one()
    dec = (await session.execute(select(DecisionType))).scalar_one()
    from bot.handlers.article_check import (
        handle_action_category, handle_action_decision, handle_action_problem, start_action_fsm,
    )

    state = _state_for(valya.telegram_id)
    callback = AsyncMock()
    callback.from_user.id = valya.telegram_id
    await start_action_fsm(callback, item.id, session, state)
    await handle_action_category(callback, ActCb(a="cat", id=cat.id, it=item.id), session, state)
    await handle_action_problem(callback, ActCb(a="prob", id=prob.id, it=item.id), session, state)

    intruder_cb = AsyncMock()
    intruder_cb.from_user.id = owner.telegram_id
    await handle_action_decision(intruder_cb, ActCb(a="dec", id=dec.id, it=item.id),
                                 session, state)
    _denied(intruder_cb)
    intruder_cb.message.edit_text.assert_not_awaited()
    assert await state.get_state() == ArticleActionStates.decision.state


async def test_action_comment_denies_unregistered_user_no_exception(session):
    inst, valya, _, s, item = await _start_marked_item(session)
    cat = (await session.execute(select(ArticleCategory))).scalar_one()
    prob = (await session.execute(select(ProblemType))).scalar_one()
    dec = (await session.execute(select(DecisionType))).scalar_one()
    from bot.handlers.article_check import (
        handle_action_category, handle_action_comment, handle_action_decision,
        handle_action_problem, start_action_fsm,
    )

    state = _state_for(valya.telegram_id)
    callback = AsyncMock()
    callback.from_user.id = valya.telegram_id
    await start_action_fsm(callback, item.id, session, state)
    await handle_action_category(callback, ActCb(a="cat", id=cat.id, it=item.id), session, state)
    await handle_action_problem(callback, ActCb(a="prob", id=prob.id, it=item.id), session, state)
    await handle_action_decision(callback, ActCb(a="dec", id=dec.id, it=item.id), session, state)

    intruder_msg = AsyncMock()
    intruder_msg.from_user.id = 999999999                # не зарегистрирован
    intruder_msg.text = "чужой комментарий"
    await handle_action_comment(intruder_msg, session, state)   # не должно бросить исключение
    _denied(intruder_msg)
    action_count = len((await session.execute(select(ArticleAction))).scalars().all())
    assert action_count == 0
    assert await state.get_state() == ArticleActionStates.comment.state


async def test_action_next_check_denies_foreign_registered_user(session):
    inst, valya, owner, s, item = await _start_marked_item(session)
    cat = (await session.execute(select(ArticleCategory))).scalar_one()
    prob = (await session.execute(select(ProblemType))).scalar_one()
    dec = (await session.execute(select(DecisionType))).scalar_one()
    from bot.handlers.article_check import (
        handle_action_category, handle_action_comment, handle_action_decision,
        handle_action_next_check, handle_action_problem, start_action_fsm,
    )

    state = _state_for(valya.telegram_id)
    callback = AsyncMock()
    callback.from_user.id = valya.telegram_id
    await start_action_fsm(callback, item.id, session, state)
    await handle_action_category(callback, ActCb(a="cat", id=cat.id, it=item.id), session, state)
    await handle_action_problem(callback, ActCb(a="prob", id=prob.id, it=item.id), session, state)
    await handle_action_decision(callback, ActCb(a="dec", id=dec.id, it=item.id), session, state)
    message = AsyncMock()
    message.from_user.id = valya.telegram_id
    message.text = "снизить"
    await handle_action_comment(message, session, state)

    intruder_msg = AsyncMock()
    intruder_msg.from_user.id = owner.telegram_id         # зарегистрирован, но не ответственный
    intruder_msg.text = "13.07.2026"
    await handle_action_next_check(intruder_msg, session, state)
    _denied(intruder_msg)
    action_count = len((await session.execute(select(ArticleAction))).scalars().all())
    assert action_count == 0                              # не создано чужое действие
