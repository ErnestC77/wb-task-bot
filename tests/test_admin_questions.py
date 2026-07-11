from unittest.mock import AsyncMock

import pytest

from bot.database.models import ArticleCategory, Role, Topic
from bot.database.repositories.user_repository import UserRepository
from bot.services.setting_service import SettingService


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

async def test_set_receiver_setting_validates_private_chat(session):
    from bot.handlers.admin.questions import set_receiver_setting
    owner = await UserRepository(session).upsert(telegram_id=1, name="O", role=Role.OWNER)
    target = await UserRepository(session).upsert(telegram_id=2, name="P", role=Role.PARTNER)
    with pytest.raises(ValueError):
        await set_receiver_setting(session, owner, "questions.fallback_receiver_user_id", target.id)
    target.private_chat_available = True
    await set_receiver_setting(session, owner, "questions.fallback_receiver_user_id", target.id)
    assert await SettingService(session).get("questions.fallback_receiver_user_id") == target.id


async def test_update_route_map_merges_not_overwrites(session):
    from bot.handlers.admin.questions import update_route_map
    owner = await UserRepository(session).upsert(telegram_id=1, name="O", role=Role.OWNER)
    await update_route_map(session, owner, "questions.route_by_topic", "goods", 10)
    got = await update_route_map(session, owner, "questions.route_by_topic", "logistics", 20)
    assert got == {"goods": 10, "logistics": 20}


# ---------------------------------------------------------------------------
# set_receiver_setting / update_route_map — дополнительные ветки
# ---------------------------------------------------------------------------

async def test_set_receiver_setting_rejects_unknown_key(session):
    from bot.handlers.admin.questions import set_receiver_setting
    owner = await _owner(session)
    with pytest.raises(KeyError):
        await set_receiver_setting(session, owner, "questions.route_by_topic", owner.id)


async def test_set_receiver_setting_rejects_inactive_user(session):
    from bot.handlers.admin.questions import set_receiver_setting
    owner = await _owner(session)
    target = await UserRepository(session).upsert(telegram_id=3, name="X", role=Role.MANAGER_WB)
    target.private_chat_available = True
    target.is_active = False
    await session.commit()
    with pytest.raises(ValueError):
        await set_receiver_setting(session, owner, "questions.default_receiver_user_id", target.id)


# ---------------------------------------------------------------------------
# actor-check
# ---------------------------------------------------------------------------

async def test_qst_callback_rejects_actor_without_permission(session):
    from bot.handlers.admin.questions import handle_qst_callback
    from bot.keyboards.admin.questions import QstCb

    logistic = await UserRepository(session).upsert(telegram_id=5, name="L", role=Role.LOGISTIC)
    await session.commit()

    callback = AsyncMock()
    callback.from_user.id = logistic.telegram_id
    await handle_qst_callback(callback, QstCb(a="list"), session)
    callback.answer.assert_awaited_with("Недостаточно прав", show_alert=True)


async def test_qst_callback_rejects_unregistered_user(session):
    from bot.handlers.admin.questions import handle_qst_callback
    from bot.keyboards.admin.questions import QstCb

    callback = AsyncMock()
    callback.from_user.id = 999999
    await handle_qst_callback(callback, QstCb(a="list"), session)
    callback.answer.assert_awaited_with("Недостаточно прав", show_alert=True)


# ---------------------------------------------------------------------------
# Точка входа
# ---------------------------------------------------------------------------

async def test_entry_shows_list(session):
    from bot.handlers.admin.questions import handle_questions_section
    from bot.services.admin_service import AdminService
    from bot.keyboards.admin.main import AdminCb

    owner = await _owner(session)
    await session.commit()

    callback = AsyncMock()
    svc = AdminService(session)
    await handle_questions_section(callback, AdminCb(s="qst", a="open"), session, owner, svc)
    assert "Маршрутизация вопросов" in callback.message.edit_text.await_args.args[0]


# ---------------------------------------------------------------------------
# Список / карточка
# ---------------------------------------------------------------------------

async def test_list_shows_eight_settings(session):
    from bot.handlers.admin.questions import handle_qst_callback
    from bot.keyboards.admin.questions import QstCb

    owner = await _owner(session)
    await session.commit()

    callback = AsyncMock()
    callback.from_user.id = owner.telegram_id
    await handle_qst_callback(callback, QstCb(a="list"), session)
    kb = _reply_markup(callback)
    # 8 настроек, page_size по умолчанию 10 -> все на одной странице + "Назад"
    assert len(kb.inline_keyboard) == 9


async def test_show_card_renders_key(session):
    from bot.handlers.admin.questions import handle_qst_callback
    from bot.keyboards.admin.questions import QstCb

    owner = await _owner(session)
    await session.commit()

    callback = AsyncMock()
    callback.from_user.id = owner.telegram_id
    # index 0 = questions.allow_complete_with_open_questions (сортировка по алфавиту)
    await handle_qst_callback(callback, QstCb(a="card", id=0), session)
    text = callback.message.edit_text.await_args.args[0]
    assert "questions.allow_complete_with_open_questions" in text


async def test_show_card_rejects_out_of_range_index(session):
    from bot.handlers.admin.questions import handle_qst_callback
    from bot.keyboards.admin.questions import QstCb

    owner = await _owner(session)
    await session.commit()

    callback = AsyncMock()
    callback.from_user.id = owner.telegram_id
    await handle_qst_callback(callback, QstCb(a="card", id=999), session)
    assert "Неизвестная настройка" in callback.answer.await_args.args[0]


# ---------------------------------------------------------------------------
# Пикер получателя
# ---------------------------------------------------------------------------

async def test_edit_receiver_key_shows_picker_with_only_private_chat_users(session):
    from bot.handlers.admin.questions import handle_qst_callback
    from bot.keyboards.admin.questions import QstCb

    owner = await _owner(session)
    closed = await UserRepository(session).upsert(telegram_id=6, name="Closed", role=Role.PARTNER)
    opened = await UserRepository(session).upsert(telegram_id=7, name="Opened", role=Role.PARTNER)
    opened.private_chat_available = True
    await session.commit()

    callback = AsyncMock()
    callback.from_user.id = owner.telegram_id
    # index 1 = questions.default_receiver_user_id
    await handle_qst_callback(callback, QstCb(a="edit", id=1), session, state=FakeState())
    kb = _reply_markup(callback)
    labels = [btn.text for row in kb.inline_keyboard for btn in row]
    assert "Opened" in labels and "Closed" not in labels


async def test_edit_receiver_key_alerts_when_no_candidates(session):
    from bot.handlers.admin.questions import handle_qst_callback
    from bot.keyboards.admin.questions import QstCb

    owner = await _owner(session)
    await session.commit()

    callback = AsyncMock()
    callback.from_user.id = owner.telegram_id
    await handle_qst_callback(callback, QstCb(a="edit", id=1), session, state=FakeState())
    callback.answer.assert_awaited_with(
        "Нет активных пользователей с открытой личкой (/start)", show_alert=True)


async def test_setrecv_applies_and_returns_to_card(session):
    from bot.handlers.admin.questions import handle_qst_callback
    from bot.keyboards.admin.questions import QstCb

    owner = await _owner(session)
    target = await UserRepository(session).upsert(telegram_id=8, name="T", role=Role.PARTNER)
    target.private_chat_available = True
    await session.commit()

    callback = AsyncMock()
    callback.from_user.id = owner.telegram_id
    await handle_qst_callback(
        callback, QstCb(a="setrecv", id=1, id2=target.id), session, state=FakeState())
    assert await SettingService(session).get("questions.default_receiver_user_id") == target.id
    text = callback.message.edit_text.await_args.args[0]
    assert "questions.default_receiver_user_id" in text


# ---------------------------------------------------------------------------
# Пикер маршрута (по теме / по категории)
# ---------------------------------------------------------------------------

async def test_edit_route_by_topic_lists_active_topics(session):
    from bot.handlers.admin.questions import handle_qst_callback
    from bot.keyboards.admin.questions import QstCb

    owner = await _owner(session)
    session.add_all([
        Topic(topic_key="goods", topic_name="Товары", message_thread_id=1, is_active=True),
        Topic(topic_key="old", topic_name="Старая", message_thread_id=2, is_active=False),
    ])
    await session.commit()

    callback = AsyncMock()
    callback.from_user.id = owner.telegram_id
    # index 7 = questions.route_by_topic
    await handle_qst_callback(callback, QstCb(a="edit", id=7), session, state=FakeState())
    kb = _reply_markup(callback)
    labels = [btn.text for row in kb.inline_keyboard for btn in row]
    assert "goods" in labels and "old" not in labels


async def test_edit_route_by_category_lists_active_categories(session):
    from bot.handlers.admin.questions import handle_qst_callback
    from bot.keyboards.admin.questions import QstCb

    owner = await _owner(session)
    session.add_all([
        ArticleCategory(name="Тест", sort_order=0, is_active=True),
        ArticleCategory(name="Архив", sort_order=1, is_active=False),
    ])
    await session.commit()

    callback = AsyncMock()
    callback.from_user.id = owner.telegram_id
    # index 6 = questions.route_by_category
    await handle_qst_callback(callback, QstCb(a="edit", id=6), session, state=FakeState())
    kb = _reply_markup(callback)
    labels = [btn.text for row in kb.inline_keyboard for btn in row]
    assert "Тест" in labels and "Архив" not in labels


async def test_routekey_then_routeset_full_flow(session):
    from bot.handlers.admin.questions import handle_qst_callback
    from bot.keyboards.admin.questions import QstCb

    owner = await _owner(session)
    target = await UserRepository(session).upsert(telegram_id=9, name="R", role=Role.PARTNER)
    target.private_chat_available = True
    session.add(Topic(topic_key="goods", topic_name="Товары", message_thread_id=1, is_active=True))
    await session.commit()

    callback = AsyncMock()
    callback.from_user.id = owner.telegram_id
    await handle_qst_callback(
        callback, QstCb(a="routekey", id=7, k="goods"), session, state=FakeState())
    kb = _reply_markup(callback)
    labels = [btn.text for row in kb.inline_keyboard for btn in row]
    assert "R" in labels

    await handle_qst_callback(
        callback, QstCb(a="routeset", id=7, k="goods", id2=target.id), session, state=FakeState())
    assert await SettingService(session).get("questions.route_by_topic") == {"goods": target.id}


async def test_routeset_rejects_non_route_key(session):
    from bot.handlers.admin.questions import handle_qst_callback
    from bot.keyboards.admin.questions import QstCb

    owner = await _owner(session)
    target = await UserRepository(session).upsert(telegram_id=10, name="R2", role=Role.PARTNER)
    target.private_chat_available = True
    await session.commit()

    callback = AsyncMock()
    callback.from_user.id = owner.telegram_id
    # index 1 = default_receiver_user_id, НЕ маршрут
    await handle_qst_callback(
        callback, QstCb(a="routeset", id=1, k="goods", id2=target.id), session, state=FakeState())
    callback.answer.assert_awaited_with("Недопустимый ключ маршрута", show_alert=True)


async def test_edit_route_by_category_alerts_when_no_active_categories(session):
    from bot.handlers.admin.questions import handle_qst_callback
    from bot.keyboards.admin.questions import QstCb

    owner = await _owner(session)
    await session.commit()

    callback = AsyncMock()
    callback.from_user.id = owner.telegram_id
    # index 6 = questions.route_by_category, нет ни одной категории
    await handle_qst_callback(callback, QstCb(a="edit", id=6), session, state=FakeState())
    callback.answer.assert_awaited_with("Нет доступных вариантов для маршрута", show_alert=True)


async def test_routeset_rejects_stale_token(session):
    """Regression: если тему/категорию удалили/деактивировали между показом
    списка и кликом, токен резолвится в None — не должен молча записаться
    как бизнес-ключ маршрута."""
    from bot.handlers.admin.questions import handle_qst_callback
    from bot.keyboards.admin.questions import QstCb

    owner = await _owner(session)
    target = await UserRepository(session).upsert(telegram_id=12, name="R3", role=Role.PARTNER)
    target.private_chat_available = True
    await session.commit()

    callback = AsyncMock()
    callback.from_user.id = owner.telegram_id
    # index 7 = route_by_topic, "ghost" не существует ни в одной теме
    await handle_qst_callback(
        callback, QstCb(a="routeset", id=7, k="ghost", id2=target.id), session, state=FakeState())
    callback.answer.assert_awaited_with("Тема/категория больше недоступна", show_alert=True)
    assert await SettingService(session).get("questions.route_by_topic") == {}


async def test_route_by_category_flow_handles_colon_in_category_name(session):
    """Regression (review finding): имя категории редактируется свободным
    текстом в Task 30 и может содержать ':', что раньше ломало QstCb.pack()
    (aiogram резервирует ':' как разделитель полей). Теперь в CallbackData
    передаётся id категории, а не имя, поэтому маршрут по категории с ':' в
    имени должен работать сквозным потоком routekey -> routeset."""
    from bot.handlers.admin.questions import handle_qst_callback
    from bot.keyboards.admin.questions import QstCb

    owner = await _owner(session)
    target = await UserRepository(session).upsert(telegram_id=13, name="R4", role=Role.PARTNER)
    target.private_chat_available = True
    cat = ArticleCategory(name="Опт: розница", sort_order=0, is_active=True)
    session.add(cat)
    await session.commit()

    callback = AsyncMock()
    callback.from_user.id = owner.telegram_id
    # index 6 = questions.route_by_category
    await handle_qst_callback(callback, QstCb(a="edit", id=6), session, state=FakeState())
    kb = _reply_markup(callback)
    labels = [btn.text for row in kb.inline_keyboard for btn in row]
    assert "Опт: розница" in labels
    token = QstCb.unpack(kb.inline_keyboard[0][0].callback_data).k
    assert token == str(cat.id)

    await handle_qst_callback(
        callback, QstCb(a="routekey", id=6, k=token), session, state=FakeState())
    text = callback.message.edit_text.await_args.args[0]
    assert "Опт: розница" in text

    await handle_qst_callback(
        callback, QstCb(a="routeset", id=6, k=token, id2=target.id), session, state=FakeState())
    assert await SettingService(session).get("questions.route_by_category") == {
        "Опт: розница": target.id}


# ---------------------------------------------------------------------------
# Обычный текстовый FSM-ввод (escalation_hours и т.п.)
# ---------------------------------------------------------------------------

async def test_edit_plain_key_starts_fsm(session):
    from bot.handlers.admin.questions import handle_qst_callback
    from bot.keyboards.admin.questions import QstCb
    from bot.states.admin_states import AdminStates

    owner = await _owner(session)
    await session.commit()

    callback = AsyncMock()
    callback.from_user.id = owner.telegram_id
    state = FakeState()
    # index 2 = questions.escalation_hours
    await handle_qst_callback(callback, QstCb(a="edit", id=2), session, state=state)
    assert state.state == AdminStates.waiting_qst_value
    assert state.data["setting_key"] == "questions.escalation_hours"


async def test_qst_value_message_applies_and_clears_state(session):
    from bot.handlers.admin.questions import handle_qst_value_message
    from bot.states.admin_states import AdminStates

    owner = await _owner(session)
    await session.commit()

    state = FakeState()
    await state.set_state(AdminStates.waiting_qst_value)
    await state.update_data(setting_key="questions.escalation_hours", idx=2)

    message = AsyncMock()
    message.from_user.id = owner.telegram_id
    message.text = "6"
    await handle_qst_value_message(message, session, state)
    assert await SettingService(session).get("questions.escalation_hours") == 6
    assert state.state is None


async def test_qst_value_message_rejects_actor_without_permission(session):
    from bot.handlers.admin.questions import handle_qst_value_message
    from bot.states.admin_states import AdminStates

    logistic = await UserRepository(session).upsert(telegram_id=11, name="L2", role=Role.LOGISTIC)
    await session.commit()

    state = FakeState()
    await state.set_state(AdminStates.waiting_qst_value)
    await state.update_data(setting_key="questions.escalation_hours", idx=2)

    message = AsyncMock()
    message.from_user.id = logistic.telegram_id
    message.text = "6"
    await handle_qst_value_message(message, session, state)
    message.answer.assert_awaited_with("Недостаточно прав")
    assert state.state is None


# ---------------------------------------------------------------------------
# Regression (Task 28/30 lesson): навигация ("list"/"card") обязана сбрасывать
# FSM, иначе следующий текст молча применился бы как значение.
# ---------------------------------------------------------------------------

async def test_card_navigation_clears_pending_fsm_state(session):
    from bot.handlers.admin.questions import handle_qst_callback
    from bot.keyboards.admin.questions import QstCb
    from bot.states.admin_states import AdminStates

    owner = await _owner(session)
    await session.commit()

    state = FakeState()
    await state.set_state(AdminStates.waiting_qst_value)
    await state.update_data(setting_key="questions.escalation_hours")

    callback = AsyncMock()
    callback.from_user.id = owner.telegram_id
    await handle_qst_callback(callback, QstCb(a="card", id=2), session, state=state)
    assert state.state is None


# ---------------------------------------------------------------------------
# Regression: реальный .pack()/.unpack() round-trip для каждой клавиатуры
# (Tasks 24-25 lesson: строковое поле с дефолтом должно быть `| None`, иначе
# unpack() ломается на пустой строке).
# ---------------------------------------------------------------------------

def test_qstcb_default_fields_roundtrip():
    from bot.keyboards.admin.questions import QstCb

    cb = QstCb(a="noop")
    unpacked = QstCb.unpack(cb.pack())
    assert unpacked.a == "noop" and unpacked.id == 0 and unpacked.id2 == 0
    assert unpacked.k is None and unpacked.p == 1


def test_qstcb_k_field_roundtrip_when_set():
    from bot.keyboards.admin.questions import QstCb

    cb = QstCb(a="routekey", id=7, k="goods")
    unpacked = QstCb.unpack(cb.pack())
    assert unpacked.k == "goods"


def test_questions_list_keyboard_roundtrip():
    from bot.keyboards.admin.questions import QstCb, questions_list_keyboard
    from bot.keyboards.admin.main import AdminCb

    entries = [(0, "a = 1"), (1, "b = 2")]
    kb = questions_list_keyboard(entries, page=1, total_pages=2)
    for row in kb.inline_keyboard[:2]:
        cb = QstCb.unpack(row[0].callback_data)
        assert cb.a == "card"
    pager = kb.inline_keyboard[2]
    assert len(pager) == 3
    for btn in pager:
        QstCb.unpack(btn.callback_data)
    back = AdminCb.unpack(kb.inline_keyboard[-1][0].callback_data)
    assert back.s == "qst" and back.a == "menu"


def test_question_setting_card_keyboard_roundtrip():
    from bot.keyboards.admin.questions import QstCb, question_setting_card_keyboard

    kb = question_setting_card_keyboard(3, True)
    for row in kb.inline_keyboard:
        for btn in row:
            QstCb.unpack(btn.callback_data)
    labels = [btn.text for row in kb.inline_keyboard for btn in row]
    assert "✏ Изменить" in labels


def test_receiver_picker_keyboard_roundtrip():
    from bot.keyboards.admin.questions import QstCb, receiver_picker_keyboard

    kb = receiver_picker_keyboard(1, [(10, "A"), (20, "B")])
    for row in kb.inline_keyboard[:2]:
        cb = QstCb.unpack(row[0].callback_data)
        assert cb.a == "setrecv" and cb.id == 1
    back = QstCb.unpack(kb.inline_keyboard[-1][0].callback_data)
    assert back.a == "card" and back.id == 1


def test_route_key_picker_keyboard_roundtrip():
    from bot.keyboards.admin.questions import QstCb, route_key_picker_keyboard

    kb = route_key_picker_keyboard(7, [("goods", "goods"), ("42", "Тест: категория")])
    for row in kb.inline_keyboard[:2]:
        cb = QstCb.unpack(row[0].callback_data)
        assert cb.a == "routekey" and cb.id == 7
    # regression: токен категории — id, а не имя (которое может содержать ":")
    assert QstCb.unpack(kb.inline_keyboard[1][0].callback_data).k == "42"


def test_route_user_picker_keyboard_roundtrip():
    from bot.keyboards.admin.questions import QstCb, route_user_picker_keyboard

    kb = route_user_picker_keyboard(7, "goods", [(10, "A")])
    cb = QstCb.unpack(kb.inline_keyboard[0][0].callback_data)
    assert cb.a == "routeset" and cb.id == 7 and cb.k == "goods" and cb.id2 == 10


def test_cancel_keyboard_roundtrip():
    from bot.keyboards.admin.questions import QstCb, cancel_keyboard

    cb = QstCb.unpack(cancel_keyboard(2).inline_keyboard[0][0].callback_data)
    assert cb.a == "card" and cb.id == 2
