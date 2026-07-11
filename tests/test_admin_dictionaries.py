from datetime import date
from unittest.mock import AsyncMock

from sqlalchemy import select

from bot.database.models import (
    ArticleCategory, CheckStatus, DecisionType, ProblemDecisionLink, ProblemType, Role,
)
from bot.database.repositories.article_check_repository import ArticleCheckRepository
from bot.database.repositories.user_repository import UserRepository
from bot.services.article_check_service import ArticleCheckService
from tests.test_article_check_service import seed


async def _owner(session):
    return await UserRepository(session).upsert(telegram_id=1, name="O", role=Role.OWNER)


class FakeState:
    def __init__(self):
        self.data: dict = {}
        self.state = None

    async def get_data(self):
        return dict(self.data)

    async def update_data(self, **kwargs):
        self.data.update(kwargs)

    async def set_state(self, state):
        self.state = state

    async def clear(self):
        self.data = {}
        self.state = None


def _reply_markup(callback):
    return callback.message.edit_text.await_args.kwargs.get("reply_markup") \
        or callback.message.edit_text.await_args.args[1]


# ---------------------------------------------------------------------------
# Step 1 (брифовые тесты, дословно)
# ---------------------------------------------------------------------------

async def test_reorder_swaps_sort_order(session):
    from bot.handlers.admin.dictionaries import move_entry
    owner = await UserRepository(session).upsert(telegram_id=1, name="O", role=Role.OWNER)
    session.add_all([ProblemType(name="A", sort_order=0), ProblemType(name="B", sort_order=1)])
    await session.commit()
    a = (await session.execute(select(ProblemType).where(ProblemType.name == "A"))).scalar_one()
    b = (await session.execute(select(ProblemType).where(ProblemType.name == "B"))).scalar_one()
    await move_entry(session, owner, "problem_types", a.id, direction="down")
    await session.commit()
    assert a.sort_order == 1 and b.sort_order == 0


async def test_delete_button_absent_for_used_entry(session):
    from bot.handlers.admin.dictionaries import can_delete
    inst, valya, _ = await seed(session, n_articles=1)
    svc = ArticleCheckService(session)
    s = await svc.start_check(inst, valya)
    item = (await ArticleCheckRepository(session).get_items_for_batch(s.id, 1, 15))[0]
    await svc.mark(item.id, 1, CheckStatus.ACTION_REQUIRED, valya)
    cat = (await session.execute(select(ArticleCategory))).scalar_one()
    prob = (await session.execute(select(ProblemType))).scalar_one()
    dec = (await session.execute(select(DecisionType))).scalar_one()
    await svc.create_action(item.id, valya, cat.id, prob.id, dec.id, None, date(2026, 7, 13))
    await session.commit()
    assert await can_delete(session, "problem_types", prob.id) is False
    fresh = ProblemType(name="Новая", sort_order=99)
    session.add(fresh)
    await session.commit()
    assert await can_delete(session, "problem_types", fresh.id) is True


async def test_link_recommended_decisions(session):
    from bot.handlers.admin.dictionaries import toggle_link
    owner = await UserRepository(session).upsert(telegram_id=1, name="O", role=Role.OWNER)
    session.add_all([ProblemType(name="CPL"), DecisionType(name="Снизить ставку")])
    await session.commit()
    prob = (await session.execute(select(ProblemType))).scalar_one()
    dec = (await session.execute(select(DecisionType))).scalar_one()
    await toggle_link(session, owner, prob.id, dec.id)
    await session.commit()
    assert (await session.scalar(select(ProblemDecisionLink))) is not None
    await toggle_link(session, owner, prob.id, dec.id)
    await session.commit()
    assert (await session.scalar(select(ProblemDecisionLink))) is None


# ---------------------------------------------------------------------------
# Доменные функции сверх брифа
# ---------------------------------------------------------------------------

async def test_delete_entry_raises_for_used_entry(session):
    from bot.handlers.admin.dictionaries import delete_entry
    import pytest
    owner = await _owner(session)
    inst, valya, _ = await seed(session, n_articles=1)
    svc = ArticleCheckService(session)
    s = await svc.start_check(inst, valya)
    item = (await ArticleCheckRepository(session).get_items_for_batch(s.id, 1, 15))[0]
    await svc.mark(item.id, 1, CheckStatus.ACTION_REQUIRED, valya)
    cat = (await session.execute(select(ArticleCategory))).scalar_one()
    prob = (await session.execute(select(ProblemType))).scalar_one()
    dec = (await session.execute(select(DecisionType))).scalar_one()
    await svc.create_action(item.id, valya, cat.id, prob.id, dec.id, None, date(2026, 7, 13))
    await session.commit()
    with pytest.raises(ValueError):
        await delete_entry(session, owner, "problem_types", prob.id)


async def test_delete_entry_removes_unused_entry(session):
    from bot.handlers.admin.dictionaries import delete_entry
    owner = await _owner(session)
    entry = ProblemType(name="Удаляемая", sort_order=0)
    session.add(entry)
    await session.commit()
    entry_id = entry.id
    await delete_entry(session, owner, "problem_types", entry_id)
    await session.commit()
    assert await session.get(ProblemType, entry_id) is None


async def test_rename_entry(session):
    from bot.handlers.admin.dictionaries import rename_entry
    owner = await _owner(session)
    entry = ProblemType(name="Старое", sort_order=0)
    session.add(entry)
    await session.commit()
    ok, _ = await rename_entry(session, owner, "problem_types", entry.id, "Новое")
    assert ok is True and entry.name == "Новое"
    ok, msg = await rename_entry(session, owner, "problem_types", entry.id, "   ")
    assert ok is False and "пуст" in msg.lower()


async def test_create_entry_extended_fields_only_for_problems_and_decisions(session):
    from bot.handlers.admin.dictionaries import create_entry
    owner = await _owner(session)
    prob = await create_entry(session, owner, "problem_types", "Новая проблема",
                              require_comment=True, default_next_check_days=5)
    await session.commit()
    assert prob.require_comment is True and prob.default_next_check_days == 5

    cat = await create_entry(session, owner, "article_categories", "Новая категория")
    await session.commit()
    assert not hasattr(cat, "require_comment") or getattr(cat, "require_comment", None) is None


# ---------------------------------------------------------------------------
# actor-check
# ---------------------------------------------------------------------------

async def test_dic_callback_rejects_actor_without_permission(session):
    from bot.handlers.admin.dictionaries import handle_dic_callback
    from bot.keyboards.admin.dictionaries import DicCb

    logistic = await UserRepository(session).upsert(telegram_id=5, name="L", role=Role.LOGISTIC)
    await session.commit()

    callback = AsyncMock()
    callback.from_user.id = logistic.telegram_id
    await handle_dic_callback(callback, DicCb(a="list", kind="problem_types"), session)
    callback.answer.assert_awaited_with("Недостаточно прав", show_alert=True)


async def test_dic_callback_rejects_unregistered_user(session):
    from bot.handlers.admin.dictionaries import handle_dic_callback
    from bot.keyboards.admin.dictionaries import DicCb

    callback = AsyncMock()
    callback.from_user.id = 999999
    await handle_dic_callback(callback, DicCb(a="list", kind="problem_types"), session)
    callback.answer.assert_awaited_with("Недостаточно прав", show_alert=True)


# ---------------------------------------------------------------------------
# Точка входа dic/dic2
# ---------------------------------------------------------------------------

async def test_entry_dic_shows_problem_types(session):
    from bot.handlers.admin.dictionaries import handle_dictionaries_section
    from bot.services.admin_service import AdminService
    from bot.keyboards.admin.main import AdminCb

    owner = await _owner(session)
    session.add(ProblemType(name="Проблема X", sort_order=0))
    await session.commit()

    callback = AsyncMock()
    svc = AdminService(session)
    await handle_dictionaries_section(callback, AdminCb(s="dic", a="open"), session, owner, svc)
    assert callback.message.edit_text.await_args.args[0].startswith("⚠")


async def test_entry_dic2_shows_decision_types(session):
    from bot.handlers.admin.dictionaries import handle_dictionaries_section
    from bot.services.admin_service import AdminService
    from bot.keyboards.admin.main import AdminCb

    owner = await _owner(session)
    await session.commit()

    callback = AsyncMock()
    svc = AdminService(session)
    await handle_dictionaries_section(callback, AdminCb(s="dic2", a="open"), session, owner, svc)
    assert callback.message.edit_text.await_args.args[0].startswith("🛠")


# ---------------------------------------------------------------------------
# Список / карточка / переключатель справочников
# ---------------------------------------------------------------------------

async def test_list_shows_inactive_with_label(session):
    from bot.handlers.admin.dictionaries import handle_dic_callback
    from bot.keyboards.admin.dictionaries import DicCb

    owner = await _owner(session)
    session.add_all([ProblemType(name="Активная", sort_order=0, is_active=True),
                     ProblemType(name="Неактивная", sort_order=1, is_active=False)])
    await session.commit()

    callback = AsyncMock()
    callback.from_user.id = owner.telegram_id
    await handle_dic_callback(callback, DicCb(a="list", kind="problem_types"), session)
    kb = _reply_markup(callback)
    labels = [btn.text for row in kb.inline_keyboard for btn in row]
    assert any("Неактивная (неактивна)" == label for label in labels)


async def test_kind_switcher_navigates_to_article_categories(session):
    from bot.handlers.admin.dictionaries import handle_dic_callback
    from bot.keyboards.admin.dictionaries import DicCb

    owner = await _owner(session)
    await session.commit()

    callback = AsyncMock()
    callback.from_user.id = owner.telegram_id
    await handle_dic_callback(callback, DicCb(a="list", kind="article_categories"), session)
    text = callback.message.edit_text.await_args.args[0]
    assert "Категории товаров" in text


async def test_show_card_renders_extended_fields_for_problem(session):
    from bot.handlers.admin.dictionaries import handle_dic_callback
    from bot.keyboards.admin.dictionaries import DicCb

    owner = await _owner(session)
    entry = ProblemType(name="Проблема", sort_order=0, require_comment=True,
                        default_next_check_days=3)
    session.add(entry)
    await session.commit()

    callback = AsyncMock()
    callback.from_user.id = owner.telegram_id
    await handle_dic_callback(callback, DicCb(a="card", kind="problem_types", id=entry.id), session)
    text = callback.message.edit_text.await_args.args[0]
    assert "Обязательный комментарий: да" in text and "3" in text


async def test_show_card_unknown_entry_rejected(session):
    from bot.handlers.admin.dictionaries import handle_dic_callback
    from bot.keyboards.admin.dictionaries import DicCb

    owner = await _owner(session)
    callback = AsyncMock()
    callback.from_user.id = owner.telegram_id
    await handle_dic_callback(callback, DicCb(a="card", kind="problem_types", id=999999), session)
    callback.answer.assert_awaited_with("Запись не найдена", show_alert=True)


async def test_card_hides_delete_button_for_used_entry(session):
    from bot.handlers.admin.dictionaries import handle_dic_callback
    from bot.keyboards.admin.dictionaries import DicCb

    owner = await _owner(session)
    inst, valya, _ = await seed(session, n_articles=1)
    svc = ArticleCheckService(session)
    s = await svc.start_check(inst, valya)
    item = (await ArticleCheckRepository(session).get_items_for_batch(s.id, 1, 15))[0]
    await svc.mark(item.id, 1, CheckStatus.ACTION_REQUIRED, valya)
    cat = (await session.execute(select(ArticleCategory))).scalar_one()
    prob = (await session.execute(select(ProblemType))).scalar_one()
    dec = (await session.execute(select(DecisionType))).scalar_one()
    await svc.create_action(item.id, valya, cat.id, prob.id, dec.id, None, date(2026, 7, 13))
    await session.commit()

    callback = AsyncMock()
    callback.from_user.id = owner.telegram_id
    await handle_dic_callback(callback, DicCb(a="card", kind="problem_types", id=prob.id), session)
    kb = _reply_markup(callback)
    labels = [btn.text for row in kb.inline_keyboard for btn in row]
    assert "🗑 Удалить" not in labels


# ---------------------------------------------------------------------------
# Переименование / отмена сбрасывает FSM
# ---------------------------------------------------------------------------

async def test_rename_via_fsm_message(session):
    from bot.handlers.admin.dictionaries import handle_dic_callback, handle_dic_rename_message
    from bot.keyboards.admin.dictionaries import DicCb

    owner = await _owner(session)
    entry = ProblemType(name="Старое имя", sort_order=0)
    session.add(entry)
    await session.commit()

    state = FakeState()
    callback = AsyncMock()
    callback.from_user.id = owner.telegram_id
    await handle_dic_callback(callback, DicCb(a="rename", kind="problem_types", id=entry.id),
                              session, state)
    assert state.state is not None

    message = AsyncMock()
    message.from_user.id = owner.telegram_id
    message.text = "Новое имя"
    await handle_dic_rename_message(message, session, state)
    assert entry.name == "Новое имя"
    assert state.state is None


async def test_navigation_clears_fsm_state(session):
    from bot.handlers.admin.dictionaries import handle_dic_callback
    from bot.keyboards.admin.dictionaries import DicCb
    from bot.states.admin_states import AdminStates

    owner = await _owner(session)
    entry = ProblemType(name="X", sort_order=0)
    session.add(entry)
    await session.commit()

    state = FakeState()
    callback = AsyncMock()
    callback.from_user.id = owner.telegram_id
    await handle_dic_callback(callback, DicCb(a="rename", kind="problem_types", id=entry.id),
                              session, state)
    assert state.state == AdminStates.waiting_dic_rename

    await handle_dic_callback(callback, DicCb(a="card", kind="problem_types", id=entry.id),
                              session, state)
    assert state.state is None and state.data == {}
    assert entry.name == "X"


# ---------------------------------------------------------------------------
# Добавление (полный мастер для problem_types, короткий для article_categories)
# ---------------------------------------------------------------------------

async def test_add_wizard_full_flow_for_problem_type(session):
    from bot.handlers.admin.dictionaries import handle_dic_add_message, handle_dic_callback
    from bot.keyboards.admin.dictionaries import DicCb

    owner = await _owner(session)
    await session.commit()

    state = FakeState()
    callback = AsyncMock()
    callback.from_user.id = owner.telegram_id
    await handle_dic_callback(callback, DicCb(a="add", kind="problem_types"), session, state)
    assert state.data["step"] == "name"

    m1 = AsyncMock()
    m1.from_user.id = owner.telegram_id
    m1.text = "Новая проблема"
    await handle_dic_add_message(m1, session, state)
    assert state.data["step"] == "require_comment"

    await handle_dic_callback(callback, DicCb(a="add_rc", kind="problem_types", id=1), session, state)
    assert state.data["step"] == "default_next_check_days" and state.data["require_comment"] is True

    m2 = AsyncMock()
    m2.from_user.id = owner.telegram_id
    m2.text = "7"
    await handle_dic_add_message(m2, session, state)
    assert state.state is None

    created = (await session.execute(
        select(ProblemType).where(ProblemType.name == "Новая проблема"))).scalar_one()
    assert created.require_comment is True and created.default_next_check_days == 7


async def test_add_wizard_short_flow_for_article_category(session):
    from bot.handlers.admin.dictionaries import handle_dic_add_message, handle_dic_callback
    from bot.keyboards.admin.dictionaries import DicCb

    owner = await _owner(session)
    await session.commit()

    state = FakeState()
    callback = AsyncMock()
    callback.from_user.id = owner.telegram_id
    await handle_dic_callback(callback, DicCb(a="add", kind="article_categories"), session, state)

    m1 = AsyncMock()
    m1.from_user.id = owner.telegram_id
    m1.text = "Новая категория"
    await handle_dic_add_message(m1, session, state)
    assert state.state is None                    # без доп. шагов — сразу создано

    created = (await session.execute(
        select(ArticleCategory).where(ArticleCategory.name == "Новая категория"))).scalar_one()
    assert created.sort_order >= 0


# ---------------------------------------------------------------------------
# Переставить / деактивировать-восстановить (мгновенно, без confirm_token)
# ---------------------------------------------------------------------------

async def test_move_via_callback_updates_sort_order(session):
    from bot.handlers.admin.dictionaries import handle_dic_callback
    from bot.keyboards.admin.dictionaries import DicCb

    owner = await _owner(session)
    a = ProblemType(name="A", sort_order=0)
    b = ProblemType(name="B", sort_order=1)
    session.add_all([a, b])
    await session.commit()

    callback = AsyncMock()
    callback.from_user.id = owner.telegram_id
    await handle_dic_callback(callback, DicCb(a="down", kind="problem_types", id=a.id), session)
    assert a.sort_order == 1 and b.sort_order == 0


async def test_toggle_active_is_immediate_no_confirm(session):
    from bot.handlers.admin.dictionaries import handle_dic_callback
    from bot.keyboards.admin.dictionaries import DicCb

    owner = await _owner(session)
    entry = ProblemType(name="X", sort_order=0, is_active=True)
    session.add(entry)
    await session.commit()

    callback = AsyncMock()
    callback.from_user.id = owner.telegram_id
    await handle_dic_callback(callback, DicCb(a="toggle", kind="problem_types", id=entry.id), session)
    assert entry.is_active is False                # деактивация БЕЗ подтверждения — брифовое решение

    await handle_dic_callback(callback, DicCb(a="toggle", kind="problem_types", id=entry.id), session)
    assert entry.is_active is True


# ---------------------------------------------------------------------------
# Удаление — через confirm_token (отклонение сверх брифа)
# ---------------------------------------------------------------------------

async def test_delete_goes_through_confirm_token_not_immediate(session):
    from bot.handlers.admin.dictionaries import handle_dic_callback
    from bot.keyboards.admin.dictionaries import DicCb

    owner = await _owner(session)
    entry = ProblemType(name="Удаляемая", sort_order=0)
    session.add(entry)
    entry_id_holder = entry
    await session.commit()

    callback = AsyncMock()
    callback.from_user.id = owner.telegram_id
    await handle_dic_callback(callback, DicCb(a="delete", kind="problem_types", id=entry.id), session)
    assert await session.get(ProblemType, entry_id_holder.id) is not None   # ещё не удалена
    kb = _reply_markup(callback)
    assert kb is not None


async def test_delete_direct_callback_rejected_for_used_entry_defense_in_depth(session):
    """Кнопка удаления скрыта в UI для используемых записей, но прямой
    (например, подделанный) callback всё равно обязан быть безопасным."""
    from bot.handlers.admin.dictionaries import handle_dic_callback
    from bot.keyboards.admin.dictionaries import DicCb

    owner = await _owner(session)
    inst, valya, _ = await seed(session, n_articles=1)
    svc = ArticleCheckService(session)
    s = await svc.start_check(inst, valya)
    item = (await ArticleCheckRepository(session).get_items_for_batch(s.id, 1, 15))[0]
    await svc.mark(item.id, 1, CheckStatus.ACTION_REQUIRED, valya)
    cat = (await session.execute(select(ArticleCategory))).scalar_one()
    prob = (await session.execute(select(ProblemType))).scalar_one()
    dec = (await session.execute(select(DecisionType))).scalar_one()
    await svc.create_action(item.id, valya, cat.id, prob.id, dec.id, None, date(2026, 7, 13))
    await session.commit()

    callback = AsyncMock()
    callback.from_user.id = owner.telegram_id
    await handle_dic_callback(callback, DicCb(a="delete", kind="problem_types", id=prob.id), session)
    callback.answer.assert_awaited_with(
        "Запись используется в истории — доступна только деактивация", show_alert=True)
    assert await session.get(ProblemType, prob.id) is not None


# ---------------------------------------------------------------------------
# Рекомендуемые решения
# ---------------------------------------------------------------------------

async def test_links_screen_marks_linked_decisions(session):
    from bot.handlers.admin.dictionaries import handle_dic_callback
    from bot.keyboards.admin.dictionaries import DicCb

    owner = await _owner(session)
    prob = ProblemType(name="Проблема", sort_order=0)
    dec1 = DecisionType(name="Решение 1", sort_order=0)
    dec2 = DecisionType(name="Решение 2", sort_order=1)
    session.add_all([prob, dec1, dec2])
    await session.commit()
    session.add(ProblemDecisionLink(problem_type_id=prob.id, decision_type_id=dec1.id))
    await session.commit()

    callback = AsyncMock()
    callback.from_user.id = owner.telegram_id
    await handle_dic_callback(callback, DicCb(a="links", kind="problem_types", id=prob.id), session)
    kb = _reply_markup(callback)
    labels = [btn.text for row in kb.inline_keyboard for btn in row]
    assert any("✅" in label and "Решение 1" in label for label in labels)
    assert any("▫" in label and "Решение 2" in label for label in labels)


async def test_toggle_link_via_callback(session):
    from bot.handlers.admin.dictionaries import handle_dic_callback
    from bot.keyboards.admin.dictionaries import DicCb

    owner = await _owner(session)
    prob = ProblemType(name="Проблема", sort_order=0)
    dec = DecisionType(name="Решение", sort_order=0)
    session.add_all([prob, dec])
    await session.commit()

    callback = AsyncMock()
    callback.from_user.id = owner.telegram_id
    await handle_dic_callback(
        callback, DicCb(a="link", kind="problem_types", id=prob.id, id2=dec.id), session)
    assert (await session.scalar(select(ProblemDecisionLink))) is not None


# ---------------------------------------------------------------------------
# Regression (Tasks 24-27 lesson): каждая клавиатура — реальный .pack()/
# .unpack() round-trip. Ни одно поле DicCb не содержит ":". `kind` — Literal
# (недопустимое значение не пройдёт unpack).
# ---------------------------------------------------------------------------

def test_diccb_default_fields_roundtrip():
    from bot.keyboards.admin.dictionaries import DicCb

    cb = DicCb(a="noop", kind="problem_types")
    packed = cb.pack()
    unpacked = DicCb.unpack(packed)
    assert unpacked.a == "noop" and unpacked.id == 0 and unpacked.id2 == 0 and unpacked.p == 1


def test_diccb_rejects_unknown_kind_on_unpack():
    from bot.keyboards.admin.dictionaries import DicCb
    import pytest

    fake_packed = "d:card:not_a_real_kind:1:0:1"
    with pytest.raises(Exception):
        DicCb.unpack(fake_packed)


def test_dictionary_list_keyboard_pack_unpack_roundtrip_and_switcher():
    from bot.keyboards.admin.dictionaries import DicCb, dictionary_list_keyboard
    from bot.keyboards.admin.main import AdminCb

    entries = [(i, f"Запись {i}") for i in range(1, 3)]
    kb = dictionary_list_keyboard("problem_types", entries, page=1, total_pages=1)
    for row in kb.inline_keyboard[:2]:
        cb = DicCb.unpack(row[0].callback_data)
        assert cb.a == "card" and cb.kind == "problem_types"
    add_cb = DicCb.unpack(kb.inline_keyboard[2][0].callback_data)
    assert add_cb.a == "add"
    switcher_row = kb.inline_keyboard[3]
    assert len(switcher_row) == 3
    for btn in switcher_row:
        DicCb.unpack(btn.callback_data)            # все три пункта переключателя валидны
    back = AdminCb.unpack(kb.inline_keyboard[-1][0].callback_data)
    assert back.s == "dic" and back.a == "menu"


def test_dictionary_card_keyboard_pack_unpack_roundtrip_with_and_without_delete():
    from bot.keyboards.admin.dictionaries import DicCb, dictionary_card_keyboard

    kb_with_delete = dictionary_card_keyboard("problem_types", 42, True, show_delete=True,
                                              show_links=True)
    labels = [btn.text for row in kb_with_delete.inline_keyboard for btn in row]
    assert "🗑 Удалить" in labels and "🔗 Рекомендуемые решения" in labels
    for row in kb_with_delete.inline_keyboard:
        for btn in row:
            DicCb.unpack(btn.callback_data)

    kb_no_delete = dictionary_card_keyboard("article_categories", 42, True, show_delete=False,
                                            show_links=False)
    labels2 = [btn.text for row in kb_no_delete.inline_keyboard for btn in row]
    assert "🗑 Удалить" not in labels2 and "🔗 Рекомендуемые решения" not in labels2


def test_links_keyboard_pack_unpack_roundtrip():
    from bot.keyboards.admin.dictionaries import DicCb, links_keyboard

    kb = links_keyboard(problem_id=5, entries=[(1, "A", True), (2, "B", False)])
    for row in kb.inline_keyboard[:2]:
        cb = DicCb.unpack(row[0].callback_data)
        assert cb.a == "link" and cb.id == 5
    back = DicCb.unpack(kb.inline_keyboard[-1][0].callback_data)
    assert back.a == "card" and back.id == 5


def test_yes_no_and_cancel_keyboards_pack_unpack_roundtrip():
    from bot.keyboards.admin.dictionaries import DicCb, cancel_keyboard, yes_no_keyboard

    kb = yes_no_keyboard("problem_types", "add_rc")
    yes_cb = DicCb.unpack(kb.inline_keyboard[0][0].callback_data)
    no_cb = DicCb.unpack(kb.inline_keyboard[0][1].callback_data)
    assert yes_cb.a == "add_rc" and yes_cb.id == 1
    assert no_cb.a == "add_rc" and no_cb.id == 0

    cancel_cb = DicCb.unpack(cancel_keyboard("decision_types").inline_keyboard[0][0].callback_data)
    assert cancel_cb.a == "list" and cancel_cb.kind == "decision_types"
