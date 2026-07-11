from unittest.mock import AsyncMock

from sqlalchemy import select

from bot.database.models import AdminAuditLog, Role
from bot.database.repositories.user_repository import UserRepository
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

async def test_preview_matches_manual_report(session):
    inst, valya, _ = await seed(session)
    from bot.handlers.admin.reports import preview_report
    text = await preview_report(session)
    assert "Всего задач" in text


async def test_send_now_logs_audit(session):
    inst, valya, owner = await seed(session)
    from bot.handlers.admin.reports import send_report_now
    bot = AsyncMock()
    bot.send_message.return_value = AsyncMock(message_id=1)
    await send_report_now(session, bot, owner)
    await session.commit()
    logs = list(await session.scalars(select(AdminAuditLog)))
    assert any(l.action == "report.manual_send" for l in logs)


# ---------------------------------------------------------------------------
# actor-check
# ---------------------------------------------------------------------------

async def test_rep_callback_rejects_actor_without_permission(session):
    from bot.handlers.admin.reports import handle_rep_callback
    from bot.keyboards.admin.reports import RepCb

    logistic = await UserRepository(session).upsert(telegram_id=5, name="L", role=Role.LOGISTIC)
    await session.commit()

    callback = AsyncMock()
    callback.from_user.id = logistic.telegram_id
    await handle_rep_callback(callback, RepCb(a="list"), session)
    callback.answer.assert_awaited_with("Недостаточно прав", show_alert=True)


async def test_rep_callback_rejects_unregistered_user(session):
    from bot.handlers.admin.reports import handle_rep_callback
    from bot.keyboards.admin.reports import RepCb

    callback = AsyncMock()
    callback.from_user.id = 999999
    await handle_rep_callback(callback, RepCb(a="list"), session)
    callback.answer.assert_awaited_with("Недостаточно прав", show_alert=True)


# ---------------------------------------------------------------------------
# Точка входа
# ---------------------------------------------------------------------------

async def test_entry_shows_list(session):
    from bot.handlers.admin.reports import handle_reports_section
    from bot.services.admin_service import AdminService
    from bot.keyboards.admin.main import AdminCb

    owner = await _owner(session)
    await session.commit()

    callback = AsyncMock()
    svc = AdminService(session)
    await handle_reports_section(callback, AdminCb(s="rep", a="open"), session, owner, svc)
    assert "Отчёты" in callback.message.edit_text.await_args.args[0]


async def test_entry_menu_action_shows_admin_menu(session):
    from bot.handlers.admin.reports import handle_reports_section
    from bot.services.admin_service import AdminService
    from bot.keyboards.admin.main import AdminCb

    owner = await _owner(session)
    await session.commit()

    callback = AsyncMock()
    svc = AdminService(session)
    await handle_reports_section(callback, AdminCb(s="rep", a="menu"), session, owner, svc)
    assert "Админ-панель" in callback.message.edit_text.await_args.args[0]


# ---------------------------------------------------------------------------
# Список / карточка
# ---------------------------------------------------------------------------

async def test_list_shows_twelve_settings_with_extra_buttons(session):
    from bot.handlers.admin.reports import handle_rep_callback
    from bot.keyboards.admin.reports import RepCb

    owner = await _owner(session)
    await session.commit()

    callback = AsyncMock()
    callback.from_user.id = owner.telegram_id
    await handle_rep_callback(callback, RepCb(a="list"), session)
    kb = _reply_markup(callback)
    labels = [btn.text for row in kb.inline_keyboard for btn in row]
    assert "👁 Предпросмотр" in labels
    assert "📤 Сформировать и отправить сейчас" in labels
    # 12 настроек, page_size по умолчанию 10 -> 2 страницы + пагинация + 2 кнопки + назад
    assert len(kb.inline_keyboard) == 10 + 1 + 2 + 1


async def test_show_card_renders_key(session):
    from bot.handlers.admin.reports import handle_rep_callback
    from bot.keyboards.admin.reports import RepCb

    owner = await _owner(session)
    await session.commit()

    callback = AsyncMock()
    callback.from_user.id = owner.telegram_id
    # index 0 = reports.format (сортировка по алфавиту)
    await handle_rep_callback(callback, RepCb(a="card", id=0), session)
    text = callback.message.edit_text.await_args.args[0]
    assert "reports.format" in text


async def test_show_card_rejects_out_of_range_index(session):
    from bot.handlers.admin.reports import handle_rep_callback
    from bot.keyboards.admin.reports import RepCb

    owner = await _owner(session)
    await session.commit()

    callback = AsyncMock()
    callback.from_user.id = owner.telegram_id
    await handle_rep_callback(callback, RepCb(a="card", id=999), session)
    assert "Неизвестная настройка" in callback.answer.await_args.args[0]


# ---------------------------------------------------------------------------
# Обычный текстовый FSM-ввод
# ---------------------------------------------------------------------------

async def test_edit_starts_fsm(session):
    from bot.handlers.admin.reports import handle_rep_callback
    from bot.keyboards.admin.reports import RepCb
    from bot.states.admin_states import AdminStates

    owner = await _owner(session)
    await session.commit()

    callback = AsyncMock()
    callback.from_user.id = owner.telegram_id
    # index 0 = reports.format
    state = FakeState()
    await handle_rep_callback(callback, RepCb(a="edit", id=0), session, state=state)
    assert state.state == AdminStates.waiting_rep_value
    assert state.data["setting_key"] == "reports.format"


async def test_rep_value_message_applies_and_clears_state(session):
    from bot.handlers.admin.reports import handle_rep_value_message
    from bot.services.setting_service import SettingService
    from bot.states.admin_states import AdminStates

    owner = await _owner(session)
    await session.commit()

    state = FakeState()
    await state.set_state(AdminStates.waiting_rep_value)
    await state.update_data(setting_key="reports.period_days", idx=0)

    message = AsyncMock()
    message.from_user.id = owner.telegram_id
    message.text = "14"
    await handle_rep_value_message(message, session, state)
    assert await SettingService(session).get("reports.period_days") == 14
    assert state.state is None


async def test_rep_value_message_rejects_actor_without_permission(session):
    from bot.handlers.admin.reports import handle_rep_value_message
    from bot.states.admin_states import AdminStates

    logistic = await UserRepository(session).upsert(telegram_id=11, name="L2", role=Role.LOGISTIC)
    await session.commit()

    state = FakeState()
    await state.set_state(AdminStates.waiting_rep_value)
    await state.update_data(setting_key="reports.period_days", idx=0)

    message = AsyncMock()
    message.from_user.id = logistic.telegram_id
    message.text = "14"
    await handle_rep_value_message(message, session, state)
    message.answer.assert_awaited_with("Недостаточно прав")
    assert state.state is None


# ---------------------------------------------------------------------------
# Regression (Task 28/30/31 lesson): навигация ("list"/"card") обязана
# сбрасывать FSM.
# ---------------------------------------------------------------------------

async def test_card_navigation_clears_pending_fsm_state(session):
    from bot.handlers.admin.reports import handle_rep_callback
    from bot.keyboards.admin.reports import RepCb
    from bot.states.admin_states import AdminStates

    owner = await _owner(session)
    await session.commit()

    state = FakeState()
    await state.set_state(AdminStates.waiting_rep_value)
    await state.update_data(setting_key="reports.period_days")

    callback = AsyncMock()
    callback.from_user.id = owner.telegram_id
    await handle_rep_callback(callback, RepCb(a="card", id=0), session, state=state)
    assert state.state is None


# ---------------------------------------------------------------------------
# Предпросмотр
# ---------------------------------------------------------------------------

async def test_preview_shows_report_without_sending(session):
    from bot.handlers.admin.reports import handle_rep_callback
    from bot.keyboards.admin.reports import RepCb

    owner = await _owner(session)
    await session.commit()

    callback = AsyncMock()
    callback.from_user.id = owner.telegram_id
    callback.bot = AsyncMock()
    await handle_rep_callback(callback, RepCb(a="preview"), session)
    text = callback.message.edit_text.await_args.args[0]
    assert "Всего задач" in text
    callback.bot.send_message.assert_not_awaited()


# ---------------------------------------------------------------------------
# Отправка сейчас — через confirm_token (опасная операция)
# ---------------------------------------------------------------------------

async def test_send_goes_through_confirm_token_with_reports_manage(session):
    from bot.handlers.admin.reports import handle_rep_callback
    from bot.keyboards.admin.confirm import ConfirmCb
    from bot.keyboards.admin.reports import RepCb
    from bot.database.models import AdminAuditLog

    owner = await _owner(session)
    await session.commit()

    callback = AsyncMock()
    callback.from_user.id = owner.telegram_id
    callback.bot = AsyncMock()
    callback.bot.send_message.return_value = AsyncMock(message_id=1)
    await handle_rep_callback(callback, RepCb(a="send"), session)

    # реальной отправки ещё не было — только поставлено в очередь подтверждения
    callback.bot.send_message.assert_not_awaited()

    kb = _reply_markup(callback)
    confirm_cb = ConfirmCb.unpack(kb.inline_keyboard[0][0].callback_data)

    from bot.services.admin_service import AdminService
    svc = AdminService(session)
    entry = svc.get_pending(confirm_cb.t)
    assert entry is not None
    assert entry.required_permission == "reports.manage"
    assert entry.creator_actor_id == owner.id

    ok = await svc.execute_confirmed(confirm_cb.t, session)
    await session.commit()
    assert ok is True
    callback.bot.send_message.assert_awaited()
    logs = list(await session.scalars(select(AdminAuditLog)))
    assert any(l.action == "report.manual_send" for l in logs)


# ---------------------------------------------------------------------------
# Regression: реальный .pack()/.unpack() round-trip для каждой клавиатуры
# ---------------------------------------------------------------------------

def test_repcb_default_fields_roundtrip():
    from bot.keyboards.admin.reports import RepCb

    cb = RepCb(a="noop")
    unpacked = RepCb.unpack(cb.pack())
    assert unpacked.a == "noop" and unpacked.id == 0 and unpacked.p == 1


def test_reports_list_keyboard_roundtrip():
    from bot.keyboards.admin.reports import RepCb, reports_list_keyboard
    from bot.keyboards.admin.main import AdminCb

    entries = [(0, "a = 1"), (1, "b = 2")]
    kb = reports_list_keyboard(entries, page=1, total_pages=2)
    for row in kb.inline_keyboard[:2]:
        cb = RepCb.unpack(row[0].callback_data)
        assert cb.a == "card"
    pager = kb.inline_keyboard[2]
    assert len(pager) == 3
    for btn in pager:
        RepCb.unpack(btn.callback_data)
    preview_cb = RepCb.unpack(kb.inline_keyboard[3][0].callback_data)
    assert preview_cb.a == "preview"
    send_cb = RepCb.unpack(kb.inline_keyboard[4][0].callback_data)
    assert send_cb.a == "send"
    back = AdminCb.unpack(kb.inline_keyboard[-1][0].callback_data)
    assert back.s == "rep" and back.a == "menu"


def test_report_setting_card_keyboard_roundtrip():
    from bot.keyboards.admin.reports import RepCb, report_setting_card_keyboard

    kb = report_setting_card_keyboard(3)
    for row in kb.inline_keyboard:
        for btn in row:
            RepCb.unpack(btn.callback_data)
    labels = [btn.text for row in kb.inline_keyboard for btn in row]
    assert "✏ Изменить" in labels


def test_back_to_list_keyboard_roundtrip():
    from bot.keyboards.admin.reports import RepCb, back_to_list_keyboard

    cb = RepCb.unpack(back_to_list_keyboard().inline_keyboard[0][0].callback_data)
    assert cb.a == "list"


def test_cancel_keyboard_roundtrip():
    from bot.keyboards.admin.reports import RepCb, cancel_keyboard

    cb = RepCb.unpack(cancel_keyboard(2).inline_keyboard[0][0].callback_data)
    assert cb.a == "card" and cb.id == 2
