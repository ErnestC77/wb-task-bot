from unittest.mock import AsyncMock

from bot.database.models import Role
from bot.database.repositories.topic_repository import TopicRepository
from bot.database.repositories.user_repository import UserRepository


async def _owner(session):
    return await UserRepository(session).upsert(telegram_id=1, name="O", role=Role.OWNER)


def _reply_markup(callback):
    return callback.message.edit_text.await_args.kwargs.get("reply_markup") \
        or callback.message.edit_text.await_args.args[1]


# ---------------------------------------------------------------------------
# Step 1 (брифовые тесты, дословно)
# ---------------------------------------------------------------------------

async def test_topic_thread_change_triggers_test_send_and_audit(session):
    from bot.handlers.admin.topics import apply_thread_id_change
    owner = await UserRepository(session).upsert(telegram_id=1, name="O", role=Role.OWNER)
    await TopicRepository(session).upsert("goods", "Управление товарами")
    bot = AsyncMock()
    bot.send_message.return_value = AsyncMock(message_id=5)
    ok = await apply_thread_id_change(session, bot, owner, "goods", 777)
    await session.commit()
    assert ok is True
    topic = await TopicRepository(session).get_by_key("goods")
    assert topic.message_thread_id == 777
    bot.send_message.assert_awaited()
    bot.delete_message.assert_awaited()

    from sqlalchemy import select
    from bot.database.models import AdminAuditLog
    logs = list(await session.scalars(select(AdminAuditLog)))
    assert any(l.action == "topic.set_thread" for l in logs)


async def test_failed_test_send_still_saves_but_reports_error(session):
    from bot.handlers.admin.topics import apply_thread_id_change
    owner = await UserRepository(session).upsert(telegram_id=1, name="O", role=Role.OWNER)
    await TopicRepository(session).upsert("ideas", "Идеи")
    bot = AsyncMock()
    bot.send_message.side_effect = RuntimeError("no thread")
    ok = await apply_thread_id_change(session, bot, owner, "ideas", 999)
    topic = await TopicRepository(session).get_by_key("ideas")
    assert ok is False and topic.message_thread_id == 999   # сохранено, ошибка видна


# ---------------------------------------------------------------------------
# Regression (Tasks 24-25 lesson): каждая НОВАЯ клавиатура обязана быть
# проверена реальным .pack()/.unpack() round-trip, не только юнит-тестами
# бизнес-логики. Ни одно поле TopCb НЕ должно содержать ":", и НИ ОДНО поле
# со значением по умолчанию не должно быть str="" / int=0-как-nullable-строка
# (см. docstring bot/keyboards/admin/main.py:AdminCb.k про баг Task 25).
# ---------------------------------------------------------------------------

def test_topics_list_keyboard_pack_unpack_roundtrip_many_topics_and_pagination():
    """Много тем (двузначные id) + пагинация + кнопка «Назад» на AdminCb —
    именно эта комбинация ломала .pack() в Task 24/25, если бы использовалась
    склейка через ":"."""
    from bot.keyboards.admin.main import AdminCb
    from bot.keyboards.admin.topics import TopCb, topics_list_keyboard

    entries = [(i, f"Тема {i} (неактивна)" if i % 2 else f"Тема {i}")
               for i in range(1, 16)]                      # 15 тем, id 1..15
    kb = topics_list_keyboard(entries, page=2, total_pages=2)

    topic_rows = kb.inline_keyboard[:15]
    assert len(topic_rows) == 15
    for idx, row in enumerate(topic_rows):
        cb = TopCb.unpack(row[0].callback_data)             # реальный round-trip
        assert cb.a == "card"
        assert cb.id == entries[idx][0]

    pag_row = kb.inline_keyboard[15]
    prev_cb = TopCb.unpack(pag_row[0].callback_data)
    noop_cb = TopCb.unpack(pag_row[1].callback_data)
    next_cb = TopCb.unpack(pag_row[2].callback_data)
    assert prev_cb.a == "list" and prev_cb.p == 1
    assert noop_cb.a == "noop"
    assert next_cb.a == "list" and next_cb.p == 2

    back_cb = AdminCb.unpack(kb.inline_keyboard[-1][0].callback_data)
    assert back_cb.s == "top" and back_cb.a == "menu"


def test_topics_list_keyboard_pack_unpack_roundtrip_single_page_no_pagination():
    from bot.keyboards.admin.topics import TopCb, topics_list_keyboard

    kb = topics_list_keyboard([(3, "Тема 3")], page=1, total_pages=1)
    assert len(kb.inline_keyboard) == 2                     # 1 тема + «Назад», без пагинации
    cb = TopCb.unpack(kb.inline_keyboard[0][0].callback_data)
    assert cb.a == "card" and cb.id == 3


def test_topic_card_keyboard_pack_unpack_roundtrip_active_and_inactive():
    from bot.keyboards.admin.topics import TopCb, topic_card_keyboard

    topic_id = 987654321                     # реалистичный многозначный id
    kb = topic_card_keyboard(topic_id, is_active=True)
    flat = [btn for row in kb.inline_keyboard for btn in row]
    actions = {TopCb.unpack(btn.callback_data).a: (btn, TopCb.unpack(btn.callback_data))
              for btn in flat}
    assert actions["editname"][1].id == topic_id
    assert actions["editthread"][1].id == topic_id
    assert actions["editevents"][1].id == topic_id
    assert actions["toggle"][1].id == topic_id
    assert actions["toggle"][0].text == "🚫 Деактивировать"
    assert actions["test"][1].id == topic_id
    assert actions["list"][1].a == "list" and actions["list"][1].p == 1  # «Назад»

    kb_inactive = topic_card_keyboard(topic_id, is_active=False)
    flat_inactive = [btn for row in kb_inactive.inline_keyboard for btn in row]
    toggle_btn = next(b for b in flat_inactive if TopCb.unpack(b.callback_data).a == "toggle")
    assert toggle_btn.text == "✅ Активировать"


def test_topcb_default_fields_roundtrip_without_explicit_values():
    """Специально бьём по кнопкам, где id/p остаются ДЕФОЛТНЫМИ (id=0, p=1) —
    тот самый паттерн, который в Task 25 сломал ВЕСЬ вход в админ-меню, когда
    default-поле было str="" вместо int с default. TopCb.id/.p — целочисленные
    с int-default, тот же паттерн, что уже работает в проде для AdminCb.id/.p."""
    from bot.keyboards.admin.topics import TopCb

    cb = TopCb(a="noop")
    packed = cb.pack()
    unpacked = TopCb.unpack(packed)
    assert unpacked.a == "noop"
    assert unpacked.id == 0
    assert unpacked.p == 1


# ---------------------------------------------------------------------------
# Список / карточка (интеграционно, через приватные функции модуля)
# ---------------------------------------------------------------------------

async def test_show_topics_list_renders_seeded_topics(session):
    from bot.handlers.admin.topics import _show_topics_list
    for key, name in (("general", "Общий"), ("goods", "Товары")):
        await TopicRepository(session).upsert(key, name)
    await session.commit()

    callback = AsyncMock()
    await _show_topics_list(callback, session, 1)
    reply_markup = _reply_markup(callback)
    assert len(reply_markup.inline_keyboard) >= 3           # 2 темы + «Назад»


async def test_show_card_renders_topic_fields(session):
    from bot.handlers.admin.topics import _show_card
    await TopicRepository(session).upsert("goods", "Товары", message_thread_id=42,
                                          event_types="tasks,reports")
    topic = await TopicRepository(session).get_by_key("goods")
    await session.commit()

    callback = AsyncMock()
    await _show_card(callback, session, topic.id)
    text = callback.message.edit_text.await_args.args[0]
    assert "Товары" in text and "goods" in text and "42" in text and "tasks,reports" in text


async def test_show_card_unknown_topic_answers_alert(session):
    from bot.handlers.admin.topics import _show_card

    callback = AsyncMock()
    await _show_card(callback, session, 999)
    callback.answer.assert_awaited_once()
    assert callback.answer.await_args.kwargs.get("show_alert") is True


# ---------------------------------------------------------------------------
# Деактивация — только через confirm_token с required_permission="topics.manage"
# (по аналогии с деактивацией пользователя Task 25); активация — мгновенная.
# ---------------------------------------------------------------------------

async def test_deactivate_topic_goes_through_confirm_token_not_immediate(session):
    from bot.handlers.admin.topics import _toggle_active
    from bot.keyboards.admin.confirm import ConfirmCb
    from bot.services.admin_service import AdminService

    owner = await _owner(session)
    await TopicRepository(session).upsert("goods", "Товары")
    topic = await TopicRepository(session).get_by_key("goods")
    await session.commit()

    svc = AdminService(session)
    callback = AsyncMock()
    await _toggle_active(callback, session, owner, svc, topic.id)

    reloaded = await TopicRepository(session).get_by_id(topic.id)
    assert reloaded.is_active is True                       # не деактивирована сразу

    reply_markup = _reply_markup(callback)
    token = reply_markup.inline_keyboard[0][0].callback_data
    confirm_cb = ConfirmCb.unpack(token)
    entry = svc.get_pending(confirm_cb.t)
    assert entry is not None
    assert entry.required_permission == "topics.manage"

    await svc.execute_confirmed(confirm_cb.t, session)
    reloaded = await TopicRepository(session).get_by_id(topic.id)
    assert reloaded.is_active is False


async def test_reactivate_topic_is_immediate_no_confirm_needed(session):
    from bot.handlers.admin.topics import _toggle_active
    from bot.services.admin_service import AdminService

    owner = await _owner(session)
    await TopicRepository(session).upsert("goods", "Товары", is_active=False)
    topic = await TopicRepository(session).get_by_key("goods")
    await session.commit()

    svc = AdminService(session)
    callback = AsyncMock()
    await _toggle_active(callback, session, owner, svc, topic.id)

    reloaded = await TopicRepository(session).get_by_id(topic.id)
    assert reloaded.is_active is True


# ---------------------------------------------------------------------------
# FSM: изменение названия / типа событий (без изменения message_thread_id,
# без автоматической тестовой отправки).
# ---------------------------------------------------------------------------

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


async def test_edit_name_flow_updates_and_logs_audit(session):
    from bot.handlers.admin.topics import _start_edit_name, handle_topic_name_message
    from sqlalchemy import select
    from bot.database.models import AdminAuditLog

    owner = await _owner(session)
    await TopicRepository(session).upsert("goods", "Товары")
    topic = await TopicRepository(session).get_by_key("goods")
    await session.commit()

    state = FakeState()
    callback = AsyncMock()
    callback.from_user.id = owner.telegram_id
    await _start_edit_name(callback, session, state, topic.id)
    from bot.states.admin_states import AdminStates
    assert state.state == AdminStates.waiting_topic_name

    message = AsyncMock()
    message.from_user.id = owner.telegram_id
    message.text = "Новое название"
    await handle_topic_name_message(message, session, state)

    reloaded = await TopicRepository(session).get_by_id(topic.id)
    assert reloaded.topic_name == "Новое название"
    logs = list(await session.scalars(select(AdminAuditLog)))
    assert any(l.action == "topic.name_change" for l in logs)
    assert state.data == {} and state.state is None


async def test_edit_events_flow_dash_clears_value(session):
    from bot.handlers.admin.topics import _start_edit_events, handle_topic_events_message

    owner = await _owner(session)
    await TopicRepository(session).upsert("goods", "Товары", event_types="tasks")
    topic = await TopicRepository(session).get_by_key("goods")
    await session.commit()

    state = FakeState()
    callback = AsyncMock()
    callback.from_user.id = owner.telegram_id
    await _start_edit_events(callback, session, state, topic.id)

    message = AsyncMock()
    message.from_user.id = owner.telegram_id
    message.text = "-"
    await handle_topic_events_message(message, session, state)

    reloaded = await TopicRepository(session).get_by_id(topic.id)
    assert reloaded.event_types is None


async def test_edit_events_flow_normalizes_csv_whitespace(session):
    from bot.handlers.admin.topics import _start_edit_events, handle_topic_events_message

    owner = await _owner(session)
    await TopicRepository(session).upsert("goods", "Товары")
    topic = await TopicRepository(session).get_by_key("goods")
    await session.commit()

    state = FakeState()
    callback = AsyncMock()
    callback.from_user.id = owner.telegram_id
    await _start_edit_events(callback, session, state, topic.id)

    message = AsyncMock()
    message.from_user.id = owner.telegram_id
    message.text = " tasks , reports ,, ideas "
    await handle_topic_events_message(message, session, state)

    reloaded = await TopicRepository(session).get_by_id(topic.id)
    assert reloaded.event_types == "tasks,reports,ideas"


async def test_edit_thread_flow_rejects_non_integer(session):
    from bot.handlers.admin.topics import _start_edit_thread, handle_topic_thread_message

    owner = await _owner(session)
    await TopicRepository(session).upsert("goods", "Товары")
    topic = await TopicRepository(session).get_by_key("goods")
    await session.commit()

    state = FakeState()
    callback = AsyncMock()
    callback.from_user.id = owner.telegram_id
    await _start_edit_thread(callback, session, state, topic.id)

    message = AsyncMock()
    message.from_user.id = owner.telegram_id
    message.text = "not-a-number"
    await handle_topic_thread_message(message, session, state)

    message.answer.assert_awaited()
    assert "целое число" in message.answer.await_args.args[0]
    from bot.states.admin_states import AdminStates
    assert state.state == AdminStates.waiting_topic_thread  # остаётся в FSM для повтора


# ---------------------------------------------------------------------------
# Ручная тестовая отправка («📨 Тестовая отправка»)
# ---------------------------------------------------------------------------

async def test_manual_test_send_success_updates_card(session):
    from bot.handlers.admin.topics import _manual_test
    from sqlalchemy import select
    from bot.database.models import AdminAuditLog

    owner = await _owner(session)
    await TopicRepository(session).upsert("goods", "Товары", message_thread_id=42)
    topic = await TopicRepository(session).get_by_key("goods")
    await session.commit()

    callback = AsyncMock()
    callback.bot.send_message.return_value = AsyncMock(message_id=5)
    await _manual_test(callback, session, owner, topic.id)

    callback.answer.assert_awaited_with("Тестовое сообщение отправлено ✅", show_alert=False)
    text = callback.message.edit_text.await_args.args[0]
    assert "успешно" in text
    logs = list(await session.scalars(select(AdminAuditLog)))
    assert any(l.action == "topic.test_send" and l.result == "ok" for l in logs)


async def test_manual_test_send_failure_shows_alert(session):
    from bot.handlers.admin.topics import _manual_test

    owner = await _owner(session)
    await TopicRepository(session).upsert("goods", "Товары", message_thread_id=42)
    topic = await TopicRepository(session).get_by_key("goods")
    await session.commit()

    callback = AsyncMock()
    callback.bot.send_message.side_effect = RuntimeError("boom")
    await _manual_test(callback, session, owner, topic.id)

    callback.answer.assert_awaited_with("Ошибка тестовой отправки ❌", show_alert=True)


# ---------------------------------------------------------------------------
# Точки входа / actor-check
# ---------------------------------------------------------------------------

async def test_handle_topics_section_menu_and_list(session):
    from bot.handlers.admin.topics import handle_topics_section
    from bot.keyboards.admin.main import AdminCb
    from bot.services.admin_service import AdminService

    owner = await _owner(session)
    svc = AdminService(session)

    callback = AsyncMock()
    await handle_topics_section(callback, AdminCb(s="top", a="menu"), session, owner, svc)
    callback.message.edit_text.assert_awaited_with(
        "🛠 Админ-панель", reply_markup=callback.message.edit_text.await_args.kwargs["reply_markup"])

    callback2 = AsyncMock()
    await handle_topics_section(callback2, AdminCb(s="top"), session, owner, svc)
    text = callback2.message.edit_text.await_args.args[0]
    assert text == "🗂 Темы Telegram"


async def test_top_callback_rejects_actor_without_permission(session):
    from bot.handlers.admin.topics import handle_top_callback
    from bot.keyboards.admin.topics import TopCb
    from bot.database.repositories.user_repository import UserRepository

    logistic = await UserRepository(session).upsert(telegram_id=5, name="L", role=Role.LOGISTIC)
    await session.commit()

    callback = AsyncMock()
    callback.from_user.id = logistic.telegram_id
    await handle_top_callback(callback, TopCb(a="list"), session)
    callback.answer.assert_awaited_with("Недостаточно прав", show_alert=True)
