from unittest.mock import AsyncMock, MagicMock, patch

from sqlalchemy import select

from bot.database.models import Article, Role
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

def test_mask_spreadsheet_id():
    from bot.handlers.admin.sync import mask_spreadsheet_id
    assert mask_spreadsheet_id("1AbCdEfGhIjKlMnOp89xZ") == "1AbC…89xZ"
    assert mask_spreadsheet_id("short") == "***"


async def test_dry_run_no_confirm_needed_apply_does(session):
    from bot.handlers.admin.sync import apply_sync, dry_run_sync
    owner = await UserRepository(session).upsert(telegram_id=1, name="O", role=Role.OWNER)
    report = await dry_run_sync(session, owner)
    assert "articles" in report
    assert (await session.scalar(select(Article).limit(1))) is None  # dry-run ничего не создал


async def test_toggle_auto_rebuilds_sync_job(session_factory):
    from bot.handlers.admin.sync import toggle_auto_sync
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from bot.services.scheduler_service import SchedulerService
    async with session_factory() as s:
        owner = await UserRepository(s).upsert(telegram_id=1, name="O", role=Role.OWNER)
        await s.commit()
        owner_id = owner.id
    scheduler = AsyncIOScheduler()
    svc = SchedulerService(scheduler, AsyncMock(), session_factory)
    async with session_factory() as s:
        owner = await UserRepository(s).get_by_id(owner_id)
        await toggle_auto_sync(s, owner, True, svc)
        await s.commit()
    assert scheduler.get_job("auto_sync") is not None


# ---------------------------------------------------------------------------
# last_sync_summary
# ---------------------------------------------------------------------------

async def test_last_sync_summary_none_when_never_run(session):
    from bot.handlers.admin.sync import last_sync_summary
    owner = await _owner(session)
    await session.commit()
    assert await last_sync_summary(session) is None


async def test_last_sync_summary_reads_latest_sync_run_entry(session):
    from bot.handlers.admin.sync import dry_run_sync, last_sync_summary
    owner = await _owner(session)
    await session.commit()
    await dry_run_sync(session, owner)
    await session.commit()
    summary = await last_sync_summary(session)
    assert summary is not None and "dry_run" in summary


# ---------------------------------------------------------------------------
# actor-check
# ---------------------------------------------------------------------------

async def test_syn_callback_rejects_actor_without_permission(session):
    from bot.handlers.admin.sync import handle_syn_callback
    from bot.keyboards.admin.sync import SynCb

    logistic = await UserRepository(session).upsert(telegram_id=5, name="L", role=Role.LOGISTIC)
    await session.commit()

    callback = AsyncMock()
    callback.from_user.id = logistic.telegram_id
    await handle_syn_callback(callback, SynCb(a="status"), session)
    callback.answer.assert_awaited_with("Недостаточно прав", show_alert=True)


async def test_syn_callback_rejects_unregistered_user(session):
    from bot.handlers.admin.sync import handle_syn_callback
    from bot.keyboards.admin.sync import SynCb

    callback = AsyncMock()
    callback.from_user.id = 999999
    await handle_syn_callback(callback, SynCb(a="status"), session)
    callback.answer.assert_awaited_with("Недостаточно прав", show_alert=True)


# ---------------------------------------------------------------------------
# Точка входа / статус-экран
# ---------------------------------------------------------------------------

async def test_entry_shows_status_with_masked_id(session):
    from bot.handlers.admin.sync import handle_sync_section
    from bot.services.admin_service import AdminService
    from bot.keyboards.admin.main import AdminCb

    owner = await _owner(session)
    await SettingService(session).set("sync.spreadsheet_id", "1AbCdEfGhIjKlMnOp89xZ", owner.id)
    await session.commit()

    callback = AsyncMock()
    svc = AdminService(session)
    await handle_sync_section(callback, AdminCb(s="syn", a="open"), session, owner, svc)
    text = callback.message.edit_text.await_args.args[0]
    assert "1AbC…89xZ" in text
    assert "1AbCdEfGhIjKlMnOp89xZ" not in text


async def test_entry_menu_action_shows_admin_menu(session):
    from bot.handlers.admin.sync import handle_sync_section
    from bot.services.admin_service import AdminService
    from bot.keyboards.admin.main import AdminCb

    owner = await _owner(session)
    await session.commit()

    callback = AsyncMock()
    svc = AdminService(session)
    await handle_sync_section(callback, AdminCb(s="syn", a="menu"), session, owner, svc)
    assert "Админ-панель" in callback.message.edit_text.await_args.args[0]


async def test_status_shows_unset_placeholder_when_no_spreadsheet_id(session):
    from bot.handlers.admin.sync import handle_syn_callback
    from bot.keyboards.admin.sync import SynCb

    owner = await _owner(session)
    await session.commit()

    callback = AsyncMock()
    callback.from_user.id = owner.telegram_id
    await handle_syn_callback(callback, SynCb(a="status"), session)
    text = callback.message.edit_text.await_args.args[0]
    assert "(не задано)" in text


# ---------------------------------------------------------------------------
# Список / карточка настроек sync — маскировка spreadsheet_id
# ---------------------------------------------------------------------------

async def test_list_masks_spreadsheet_id_row(session):
    from bot.handlers.admin.sync import handle_syn_callback
    from bot.keyboards.admin.sync import SynCb

    owner = await _owner(session)
    await SettingService(session).set("sync.spreadsheet_id", "1AbCdEfGhIjKlMnOp89xZ", owner.id)
    await session.commit()

    callback = AsyncMock()
    callback.from_user.id = owner.telegram_id
    await handle_syn_callback(callback, SynCb(a="list"), session)
    kb = _reply_markup(callback)
    labels = [btn.text for row in kb.inline_keyboard for btn in row]
    assert any("1AbC…89xZ" in label for label in labels)
    assert not any("1AbCdEfGhIjKlMnOp89xZ" in label for label in labels)


async def test_show_card_masks_spreadsheet_id(session):
    from bot.handlers.admin.sync import handle_syn_callback
    from bot.handlers.admin.settings import category_keys
    from bot.keyboards.admin.sync import SynCb

    owner = await _owner(session)
    await SettingService(session).set("sync.spreadsheet_id", "1AbCdEfGhIjKlMnOp89xZ", owner.id)
    await session.commit()

    idx = category_keys("sync").index("sync.spreadsheet_id")
    callback = AsyncMock()
    callback.from_user.id = owner.telegram_id
    await handle_syn_callback(callback, SynCb(a="card", id=idx), session)
    text = callback.message.edit_text.await_args.args[0]
    assert "1AbC…89xZ" in text
    assert "1AbCdEfGhIjKlMnOp89xZ" not in text


async def test_show_card_renders_other_key_unmasked(session):
    from bot.handlers.admin.sync import handle_syn_callback
    from bot.handlers.admin.settings import category_keys
    from bot.keyboards.admin.sync import SynCb

    owner = await _owner(session)
    await session.commit()

    idx = category_keys("sync").index("sync.sheet_users")
    callback = AsyncMock()
    callback.from_user.id = owner.telegram_id
    await handle_syn_callback(callback, SynCb(a="card", id=idx), session)
    text = callback.message.edit_text.await_args.args[0]
    assert "sync.sheet_users" in text


async def test_show_card_rejects_out_of_range_index(session):
    from bot.handlers.admin.sync import handle_syn_callback
    from bot.keyboards.admin.sync import SynCb

    owner = await _owner(session)
    await session.commit()

    callback = AsyncMock()
    callback.from_user.id = owner.telegram_id
    await handle_syn_callback(callback, SynCb(a="card", id=999), session)
    assert "Неизвестная настройка" in callback.answer.await_args.args[0]


# ---------------------------------------------------------------------------
# Редактирование — маскировка в prompt для spreadsheet_id
# ---------------------------------------------------------------------------

async def test_edit_spreadsheet_id_shows_masked_current_value(session):
    from bot.handlers.admin.sync import handle_syn_callback
    from bot.handlers.admin.settings import category_keys
    from bot.keyboards.admin.sync import SynCb
    from bot.states.admin_states import AdminStates

    owner = await _owner(session)
    await SettingService(session).set("sync.spreadsheet_id", "1AbCdEfGhIjKlMnOp89xZ", owner.id)
    await session.commit()

    idx = category_keys("sync").index("sync.spreadsheet_id")
    callback = AsyncMock()
    callback.from_user.id = owner.telegram_id
    state = FakeState()
    await handle_syn_callback(callback, SynCb(a="edit", id=idx), session, state=state)
    text = callback.message.edit_text.await_args.args[0]
    assert "1AbC…89xZ" in text
    assert "1AbCdEfGhIjKlMnOp89xZ" not in text
    assert state.state == AdminStates.waiting_syn_value
    assert state.data["setting_key"] == "sync.spreadsheet_id"


async def test_syn_value_message_applies_new_spreadsheet_id(session):
    from bot.handlers.admin.sync import handle_syn_value_message
    from bot.states.admin_states import AdminStates

    owner = await _owner(session)
    await session.commit()

    state = FakeState()
    await state.set_state(AdminStates.waiting_syn_value)
    await state.update_data(setting_key="sync.spreadsheet_id", idx=0)

    message = AsyncMock()
    message.from_user.id = owner.telegram_id
    message.text = "NewFullSpreadsheetIdValue123"
    await handle_syn_value_message(message, session, state)
    assert await SettingService(session).get(
        "sync.spreadsheet_id") == "NewFullSpreadsheetIdValue123"
    assert state.state is None


async def test_syn_value_message_rejects_actor_without_permission(session):
    from bot.handlers.admin.sync import handle_syn_value_message
    from bot.states.admin_states import AdminStates

    logistic = await UserRepository(session).upsert(telegram_id=11, name="L2", role=Role.LOGISTIC)
    await session.commit()

    state = FakeState()
    await state.set_state(AdminStates.waiting_syn_value)
    await state.update_data(setting_key="sync.spreadsheet_id", idx=0)

    message = AsyncMock()
    message.from_user.id = logistic.telegram_id
    message.text = "x"
    await handle_syn_value_message(message, session, state)
    message.answer.assert_awaited_with("Недостаточно прав")
    assert state.state is None


async def test_edit_interval_minutes_via_generic_editor_rebuilds_job_with_fresh_value(
        session_factory):
    """Regression (найдено ревью Task 33): apply_setting_input (settings.py,
    переиспользуется здесь) вызывала register_sync_job() ДО коммита нового
    значения — job пересобирался бы по СТАРОМУ interval_minutes. Правило
    Task 27/28. Реальный SchedulerService (не AsyncMock) нужен, чтобы
    проверить, что job реально видит СВЕЖЕЕ значение, а не просто что он
    существует."""
    from bot.handlers.admin.sync import handle_syn_value_message
    from bot.states.admin_states import AdminStates
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from bot.services.scheduler_service import SchedulerService

    async with session_factory() as s:
        owner = await UserRepository(s).upsert(telegram_id=1, name="O", role=Role.OWNER)
        await SettingService(s).set("sync.auto_enabled", True, owner.id)
        await s.commit()
        owner_id = owner.id

    scheduler = AsyncIOScheduler()
    scheduler.wb_service = SchedulerService(scheduler, AsyncMock(), session_factory)
    await scheduler.wb_service.register_sync_job()
    assert scheduler.get_job("auto_sync").trigger.interval.total_seconds() == 60 * 60  # default

    class FakeDispatcher:
        workflow_data = {"scheduler": scheduler}

    async with session_factory() as s:
        owner = await UserRepository(s).get_by_id(owner_id)
        state = FakeState()
        await state.set_state(AdminStates.waiting_syn_value)
        await state.update_data(setting_key="sync.interval_minutes", idx=0)

        message = AsyncMock()
        message.from_user.id = owner.telegram_id
        message.text = "5"
        await handle_syn_value_message(message, s, state, dispatcher=FakeDispatcher())

    assert scheduler.get_job("auto_sync").trigger.interval.total_seconds() == 5 * 60


# ---------------------------------------------------------------------------
# Regression (Task 28/30/31/32 lesson): навигация обязана сбрасывать FSM.
# ---------------------------------------------------------------------------

async def test_status_navigation_clears_pending_fsm_state(session):
    from bot.handlers.admin.sync import handle_syn_callback
    from bot.keyboards.admin.sync import SynCb
    from bot.states.admin_states import AdminStates

    owner = await _owner(session)
    await session.commit()

    state = FakeState()
    await state.set_state(AdminStates.waiting_syn_value)
    await state.update_data(setting_key="sync.spreadsheet_id")

    callback = AsyncMock()
    callback.from_user.id = owner.telegram_id
    await handle_syn_callback(callback, SynCb(a="status"), session, state=state)
    assert state.state is None


# ---------------------------------------------------------------------------
# Dry-run — без подтверждения, ничего не пишет
# ---------------------------------------------------------------------------

async def test_dryrun_callback_shows_report_without_confirm(session):
    from bot.handlers.admin.sync import handle_syn_callback
    from bot.keyboards.admin.sync import SynCb

    owner = await _owner(session)
    await session.commit()

    callback = AsyncMock()
    callback.from_user.id = owner.telegram_id
    await handle_syn_callback(callback, SynCb(a="dryrun"), session)
    text = callback.message.edit_text.await_args.args[0]
    assert "Dry-run" in text
    assert (await session.scalar(select(Article).limit(1))) is None


# ---------------------------------------------------------------------------
# Toggle — без подтверждения, пересобирает job
# ---------------------------------------------------------------------------

async def test_toggle_callback_flips_setting_without_confirm(session):
    from bot.handlers.admin.sync import handle_syn_callback
    from bot.keyboards.admin.sync import SynCb

    owner = await _owner(session)
    await session.commit()
    assert bool(await SettingService(session).get("sync.auto_enabled")) is False

    callback = AsyncMock()
    callback.from_user.id = owner.telegram_id
    await handle_syn_callback(callback, SynCb(a="toggle"), session)
    assert bool(await SettingService(session).get("sync.auto_enabled")) is True


# ---------------------------------------------------------------------------
# Apply — только через confirm_token, реальный вызов только после подтверждения
# ---------------------------------------------------------------------------

async def test_apply_goes_through_confirm_token_with_sync_run(session):
    from bot.handlers.admin.sync import handle_syn_callback
    from bot.keyboards.admin.confirm import ConfirmCb
    from bot.keyboards.admin.sync import SynCb
    from bot.services.admin_service import AdminService

    owner = await _owner(session)
    await session.commit()

    callback = AsyncMock()
    callback.from_user.id = owner.telegram_id
    await handle_syn_callback(callback, SynCb(a="apply"), session)

    kb = _reply_markup(callback)
    confirm_cb = ConfirmCb.unpack(kb.inline_keyboard[0][0].callback_data)

    svc = AdminService(session)
    entry = svc.get_pending(confirm_cb.t)
    assert entry is not None
    assert entry.required_permission == "sync.run"
    assert entry.creator_actor_id == owner.id

    fake_client = MagicMock()
    fake_client.read_rows.return_value = []
    with patch("bot.handlers.admin.sync.SheetsClient", return_value=fake_client) as mock_cls:
        ok = await svc.execute_confirmed(confirm_cb.t, session)
    await session.commit()
    assert ok is True
    mock_cls.assert_called_once()
    # пустые строки от фейкового клиента -> ничего не создано, но sync.run записан в аудит
    assert (await session.scalar(select(Article).limit(1))) is None
    from bot.handlers.admin.sync import last_sync_summary
    summary = await last_sync_summary(session)
    assert summary is not None and " — ok" in summary


# ---------------------------------------------------------------------------
# Regression: реальный .pack()/.unpack() round-trip для каждой клавиатуры
# ---------------------------------------------------------------------------

def test_syncb_default_fields_roundtrip():
    from bot.keyboards.admin.sync import SynCb

    cb = SynCb(a="noop")
    unpacked = SynCb.unpack(cb.pack())
    assert unpacked.a == "noop" and unpacked.id == 0 and unpacked.p == 1


def test_sync_status_keyboard_roundtrip_reflects_toggle_state():
    from bot.keyboards.admin.sync import SynCb, sync_status_keyboard
    from bot.keyboards.admin.main import AdminCb

    kb_off = sync_status_keyboard(auto_enabled=False)
    labels_off = [btn.text for row in kb_off.inline_keyboard for btn in row]
    assert "▶ Включить автосинхронизацию" in labels_off
    for row in kb_off.inline_keyboard[:-1]:
        for btn in row:
            SynCb.unpack(btn.callback_data)
    back = AdminCb.unpack(kb_off.inline_keyboard[-1][0].callback_data)
    assert back.s == "syn" and back.a == "menu"

    kb_on = sync_status_keyboard(auto_enabled=True)
    labels_on = [btn.text for row in kb_on.inline_keyboard for btn in row]
    assert "⏸ Выключить автосинхронизацию" in labels_on


def test_sync_list_keyboard_roundtrip():
    from bot.keyboards.admin.sync import SynCb, sync_list_keyboard

    entries = [(0, "a = 1"), (1, "b = 2")]
    kb = sync_list_keyboard(entries, page=1, total_pages=2)
    for row in kb.inline_keyboard[:2]:
        cb = SynCb.unpack(row[0].callback_data)
        assert cb.a == "card"
    pager = kb.inline_keyboard[2]
    assert len(pager) == 3
    for btn in pager:
        SynCb.unpack(btn.callback_data)
    back = SynCb.unpack(kb.inline_keyboard[-1][0].callback_data)
    assert back.a == "status"


def test_sync_card_keyboard_roundtrip():
    from bot.keyboards.admin.sync import SynCb, sync_card_keyboard

    kb = sync_card_keyboard(3)
    for row in kb.inline_keyboard:
        for btn in row:
            SynCb.unpack(btn.callback_data)
    labels = [btn.text for row in kb.inline_keyboard for btn in row]
    assert "✏ Изменить" in labels


def test_status_back_keyboard_roundtrip():
    from bot.keyboards.admin.sync import SynCb, status_back_keyboard

    cb = SynCb.unpack(status_back_keyboard().inline_keyboard[0][0].callback_data)
    assert cb.a == "status"


def test_cancel_keyboard_roundtrip():
    from bot.keyboards.admin.sync import SynCb, cancel_keyboard

    cb = SynCb.unpack(cancel_keyboard(2).inline_keyboard[0][0].callback_data)
    assert cb.a == "card" and cb.id == 2
