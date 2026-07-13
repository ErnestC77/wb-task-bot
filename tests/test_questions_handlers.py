from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage

from bot.database.models import CheckStatus, QuestionStatus, Role
from bot.database.repositories.article_check_repository import ArticleCheckRepository
from bot.database.repositories.question_repository import QuestionRepository
from bot.database.repositories.user_repository import UserRepository
from bot.keyboards.article_check_keyboards import ChkCb
from bot.keyboards.question_keyboards import QstCb
from bot.keyboards.task_keyboards import TaskCb
from bot.services.article_check_service import ArticleCheckService
from bot.services.question_service import QuestionService
from bot.services.setting_service import SettingService
from bot.states.question_states import QuestionStates
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


async def _receiver(session):
    return await UserRepository(session).upsert(telegram_id=99, name="Эксперт",
                                                role=Role.PARTNER)


# ---------------------------------------------------------------------------
# TaskCb(a="question") — общий вопрос по задаче
# ---------------------------------------------------------------------------

async def test_task_question_denies_unregistered_user(session):
    inst, valya, _ = await seed(session)
    await session.commit()
    from bot.handlers.questions import handle_task_question

    callback = AsyncMock()
    callback.from_user.id = 999999999               # не зарегистрирован
    state = _state_for(callback.from_user.id)
    await handle_task_question(callback, TaskCb(a="question", i=inst.id), session, state)
    _denied(callback)
    assert await state.get_state() is None
    callback.message.answer.assert_not_awaited()


async def test_task_question_denies_foreign_registered_user(session):
    inst, valya, owner = await seed(session)
    await session.commit()
    from bot.handlers.questions import handle_task_question

    callback = AsyncMock()
    callback.from_user.id = owner.telegram_id        # зарегистрирован, но не ответственный
    state = _state_for(callback.from_user.id)
    await handle_task_question(callback, TaskCb(a="question", i=inst.id), session, state)
    _denied(callback)
    assert await state.get_state() is None
    callback.message.answer.assert_not_awaited()


async def test_task_question_happy_path_sets_state(session):
    inst, valya, _ = await seed(session)
    await session.commit()
    from bot.handlers.questions import handle_task_question

    callback = AsyncMock()
    callback.from_user.id = valya.telegram_id
    state = _state_for(callback.from_user.id)
    await handle_task_question(callback, TaskCb(a="question", i=inst.id), session, state)
    assert await state.get_state() == QuestionStates.waiting_text.state
    data = await state.get_data()
    assert data["task_instance_id"] == inst.id and data["article_check_item_id"] is None
    callback.message.answer.assert_awaited_once()


# ---------------------------------------------------------------------------
# ChkCb(a="gq") — общий вопрос по проверке (без привязки к артикулу)
# ---------------------------------------------------------------------------

async def test_general_question_denies_foreign_registered_user(session):
    inst, valya, owner = await seed(session, n_articles=3)
    svc = ArticleCheckService(session)
    s = await svc.start_check(inst, valya)
    await session.commit()
    from bot.handlers.questions import handle_general_question

    callback = AsyncMock()
    callback.from_user.id = owner.telegram_id
    state = _state_for(callback.from_user.id)
    await handle_general_question(callback, ChkCb(a="gq", s=s.id), session, state)
    _denied(callback)
    assert await state.get_state() is None


async def test_general_question_happy_path_sets_state(session):
    inst, valya, _ = await seed(session, n_articles=3)
    svc = ArticleCheckService(session)
    s = await svc.start_check(inst, valya)
    await session.commit()
    from bot.handlers.questions import handle_general_question

    callback = AsyncMock()
    callback.from_user.id = valya.telegram_id
    state = _state_for(callback.from_user.id)
    await handle_general_question(callback, ChkCb(a="gq", s=s.id), session, state)
    assert await state.get_state() == QuestionStates.waiting_text.state
    data = await state.get_data()
    assert data["task_instance_id"] == inst.id and data["article_check_item_id"] is None


# ---------------------------------------------------------------------------
# handle_mark(st="q") -> start_article_question -> текст -> ask()
# Проверяем, что state реально прокидывается через всю цепочку (была найдена
# и исправлена дыра: article_check.py не передавал state в start_article_question).
# ---------------------------------------------------------------------------

async def test_article_question_full_flow_via_handle_mark(session):
    inst, valya, _ = await seed(session, n_articles=2)
    receiver = await _receiver(session)
    await SettingService(session).set("questions.default_receiver_user_id",
                                      receiver.id, valya.id)
    svc = ArticleCheckService(session)
    s = await svc.start_check(inst, valya)
    item = (await ArticleCheckRepository(session).get_items_for_batch(s.id, 1, 15))[0]
    await session.commit()

    from bot.handlers.article_check import handle_mark

    callback = AsyncMock()
    callback.from_user.id = valya.telegram_id
    callback.bot.send_message.return_value = SimpleNamespace(
        message_id=1, chat=SimpleNamespace(id=99))
    state = _state_for(valya.telegram_id)
    cb_data = ChkCb(a="mark", s=s.id, it=item.id, v=item.version, st="q")
    await handle_mark(callback, cb_data, session, state)

    assert await state.get_state() == QuestionStates.waiting_text.state
    data = await state.get_data()
    assert data["article_check_item_id"] == item.id

    from bot.handlers.questions import handle_question_text
    message = AsyncMock()
    message.from_user.id = valya.telegram_id
    message.text = "Нужно ли снижать цену?"
    message.bot.send_message.return_value = SimpleNamespace(
        message_id=2, chat=SimpleNamespace(id=99))
    await handle_question_text(message, session, state)

    assert await state.get_state() is None
    q = (await QuestionRepository(session).open_for_instance(inst.id))[0]
    assert q.article_check_item_id == item.id and q.to_user_id == receiver.id
    assert q.status == QuestionStatus.SENT


async def test_question_text_rejects_too_long(session):
    inst, valya, _ = await seed(session)
    receiver = await _receiver(session)
    await SettingService(session).set("questions.default_receiver_user_id",
                                      receiver.id, valya.id)
    await SettingService(session).set("general.max_question_length", 10, valya.id)
    await session.commit()

    from bot.handlers.questions import handle_task_question, handle_question_text

    callback = AsyncMock()
    callback.from_user.id = valya.telegram_id
    state = _state_for(valya.telegram_id)
    await handle_task_question(callback, TaskCb(a="question", i=inst.id), session, state)

    message = AsyncMock()
    message.from_user.id = valya.telegram_id
    message.text = "это точно больше десяти символов"
    await handle_question_text(message, session, state)
    assert await state.get_state() == QuestionStates.waiting_text.state   # не продвинулось
    text = message.answer.call_args.args[0]
    assert "длин" in text.lower()


async def test_question_text_no_receiver_configured_gives_feedback_not_silent_crash(session):
    """Regression: resolve_receiver() поднимал ValueError, если ни
    questions.default_receiver_user_id, ни fallback не настроены — раньше
    это падало необработанным исключением внутри handle_question_text, и
    пользователь не получал вообще никакого ответа ("ввожу вопрос и ничего
    не происходит")."""
    inst, valya, _ = await seed(session)
    # НЕ настраиваем questions.default_receiver_user_id/fallback — оба 0 по умолчанию

    from bot.handlers.questions import handle_question_text, handle_task_question

    callback = AsyncMock()
    callback.from_user.id = valya.telegram_id
    state = _state_for(valya.telegram_id)
    await handle_task_question(callback, TaskCb(a="question", i=inst.id), session, state)

    message = AsyncMock()
    message.from_user.id = valya.telegram_id
    message.text = "Есть проблема с поставкой"
    await handle_question_text(message, session, state)

    assert await state.get_state() is None              # FSM не завис
    message.answer.assert_awaited()
    text = message.answer.await_args.args[0]
    assert "получател" in text.lower()


# ---------------------------------------------------------------------------
# QstCb(a="ans") — критическая проверка: отвечать может только адресат
# ---------------------------------------------------------------------------

async def test_answer_button_denies_non_receiver_registered_user(session):
    inst, valya, _ = await seed(session)
    receiver = await _receiver(session)
    await SettingService(session).set("questions.default_receiver_user_id",
                                      receiver.id, valya.id)
    bot = AsyncMock()
    bot.send_message.return_value = SimpleNamespace(message_id=1, chat=SimpleNamespace(id=99))
    q = await QuestionService(session, bot).ask(inst, valya, "?")
    await session.commit()

    from bot.handlers.questions import handle_answer_button

    intruder_cb = AsyncMock()
    intruder_cb.from_user.id = valya.telegram_id     # зарегистрирован (сам автор вопроса!), но не адресат
    state = _state_for(intruder_cb.from_user.id)
    await handle_answer_button(intruder_cb, QstCb(a="ans", id=q.id), session, state)
    _denied(intruder_cb)
    assert await state.get_state() is None            # FSM не установлен


async def test_answer_button_denies_unregistered_user(session):
    inst, valya, _ = await seed(session)
    receiver = await _receiver(session)
    await SettingService(session).set("questions.default_receiver_user_id",
                                      receiver.id, valya.id)
    bot = AsyncMock()
    bot.send_message.return_value = SimpleNamespace(message_id=1, chat=SimpleNamespace(id=99))
    q = await QuestionService(session, bot).ask(inst, valya, "?")
    await session.commit()

    from bot.handlers.questions import handle_answer_button

    intruder_cb = AsyncMock()
    intruder_cb.from_user.id = 999999999
    state = _state_for(intruder_cb.from_user.id)
    await handle_answer_button(intruder_cb, QstCb(a="ans", id=q.id), session, state)
    _denied(intruder_cb)
    assert await state.get_state() is None


async def test_answer_button_denies_nonexistent_question(session):
    inst, valya, _ = await seed(session)
    from bot.handlers.questions import handle_answer_button

    callback = AsyncMock()
    callback.from_user.id = valya.telegram_id
    state = _state_for(callback.from_user.id)
    await handle_answer_button(callback, QstCb(a="ans", id=999999), session, state)
    _denied(callback)
    assert await state.get_state() is None


async def test_answer_flow_happy_path_via_handlers(session):
    inst, valya, _ = await seed(session)
    receiver = await _receiver(session)
    await SettingService(session).set("questions.default_receiver_user_id",
                                      receiver.id, valya.id)
    await SettingService(session).set("questions.notify_asker_on_answer", True, valya.id)
    inst.telegram_chat_id, inst.topic_snapshot = -100123, 5
    await session.commit()
    bot = AsyncMock()
    bot.send_message.return_value = SimpleNamespace(message_id=1, chat=SimpleNamespace(id=99))
    q = await QuestionService(session, bot).ask(inst, valya, "?")
    await session.commit()

    from bot.handlers.questions import handle_answer_button, handle_answer_text

    callback = AsyncMock()
    callback.from_user.id = receiver.telegram_id     # реальный адресат
    state = _state_for(receiver.telegram_id)
    await handle_answer_button(callback, QstCb(a="ans", id=q.id), session, state)
    assert await state.get_state() == QuestionStates.waiting_answer.state

    message = AsyncMock()
    message.from_user.id = receiver.telegram_id
    message.text = "Снижай цену"
    message.bot.send_message.return_value = SimpleNamespace(
        message_id=2, chat=SimpleNamespace(id=10))
    await handle_answer_text(message, session, state)
    assert await state.get_state() is None

    fresh = await QuestionRepository(session).get(q.id)
    assert fresh.status == QuestionStatus.ANSWERED and fresh.answer_text == "Снижай цену"
    # ответ ушёл в чат самой задачи (не личным сообщением спрашивающему —
    # с ботом в личке работает только админ), с упоминанием valya
    assert any(c.kwargs.get("chat_id") == inst.telegram_chat_id
              and "Снижай цену" in c.kwargs.get("text", "")
              for c in message.bot.send_message.await_args_list)


async def test_answer_text_denies_unregistered_user_no_exception(session):
    inst, valya, _ = await seed(session)
    receiver = await _receiver(session)
    await SettingService(session).set("questions.default_receiver_user_id",
                                      receiver.id, valya.id)
    bot = AsyncMock()
    bot.send_message.return_value = SimpleNamespace(message_id=1, chat=SimpleNamespace(id=99))
    q = await QuestionService(session, bot).ask(inst, valya, "?")
    await session.commit()

    from bot.handlers.questions import handle_answer_button, handle_answer_text

    callback = AsyncMock()
    callback.from_user.id = receiver.telegram_id
    state = _state_for(receiver.telegram_id)
    await handle_answer_button(callback, QstCb(a="ans", id=q.id), session, state)

    intruder_msg = AsyncMock()
    intruder_msg.from_user.id = 999999999
    intruder_msg.text = "чужой ответ"
    await handle_answer_text(intruder_msg, session, state)   # не должно бросить исключение
    _denied(intruder_msg)
    fresh = await QuestionRepository(session).get(q.id)
    assert fresh.status == QuestionStatus.SENT               # ничего не изменилось
