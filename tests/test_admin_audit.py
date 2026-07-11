from unittest.mock import AsyncMock

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select

from bot.database.models import AdminAuditLog, Role
from bot.database.repositories.audit_repository import AuditRepository
from bot.database.repositories.user_repository import UserRepository
from bot.services.scheduler_service import SchedulerService


async def _owner(session):
    return await UserRepository(session).upsert(telegram_id=1, name="O", role=Role.OWNER)


def _reply_markup(callback):
    return callback.message.edit_text.await_args.kwargs.get("reply_markup") \
        or callback.message.edit_text.await_args.args[1]


class FakeDispatcher:
    def __init__(self, scheduler):
        self.workflow_data = {"scheduler": scheduler}


# ---------------------------------------------------------------------------
# Step 1 (брифовые тесты, дословно) — тест 29 раздела 14
# ---------------------------------------------------------------------------

async def test_render_audit_page(session):
    owner = await UserRepository(session).upsert(telegram_id=1, name="O", role=Role.OWNER)
    await AuditRepository(session).add(actor_user_id=owner.id, action="setting.set",
                                       setting_key="approval.timeout_hours",
                                       old_value_json="24", new_value_json="48")
    await session.commit()
    from bot.handlers.admin.audit import render_audit_page
    text = await render_audit_page(session, page=1, page_size=10)
    assert "setting.set" in text and "approval.timeout_hours" in text


def test_audit_module_has_no_mutation_handlers():                # тест 29
    import inspect
    import bot.handlers.admin.audit as audit_module
    source = inspect.getsource(audit_module)
    for forbidden in ("session.delete(", "session.add(", "\"update(\"", ".edit_entry("):
        assert forbidden not in source


# ---------------------------------------------------------------------------
# render_audit_page — дополнительные ветки
# ---------------------------------------------------------------------------

async def test_render_audit_page_empty_journal(session):
    from bot.handlers.admin.audit import render_audit_page
    owner = await _owner(session)
    await session.commit()
    text = await render_audit_page(session, page=1, page_size=10)
    assert text == "Журнал пуст"


async def test_render_audit_page_filters_by_actor(session):
    from bot.handlers.admin.audit import render_audit_page
    owner = await _owner(session)
    other = await UserRepository(session).upsert(telegram_id=2, name="P", role=Role.PARTNER)
    await AuditRepository(session).add(actor_user_id=owner.id, action="a.owner")
    await AuditRepository(session).add(actor_user_id=other.id, action="a.other")
    await session.commit()
    text = await render_audit_page(session, page=1, page_size=10, actor_user_id=owner.id)
    assert "a.owner" in text and "a.other" not in text


# ---------------------------------------------------------------------------
# actor-check
# ---------------------------------------------------------------------------

async def test_aud_callback_rejects_actor_without_permission(session):
    from bot.handlers.admin.audit import handle_aud_callback
    from bot.keyboards.admin.audit import AudCb

    logistic = await UserRepository(session).upsert(telegram_id=5, name="L", role=Role.LOGISTIC)
    await session.commit()

    callback = AsyncMock()
    callback.from_user.id = logistic.telegram_id
    await handle_aud_callback(callback, AudCb(a="page"), session)
    callback.answer.assert_awaited_with("Недостаточно прав", show_alert=True)


async def test_aud_callback_rejects_unregistered_user(session):
    from bot.handlers.admin.audit import handle_aud_callback
    from bot.keyboards.admin.audit import AudCb

    callback = AsyncMock()
    callback.from_user.id = 999999
    await handle_aud_callback(callback, AudCb(a="page"), session)
    callback.answer.assert_awaited_with("Недостаточно прав", show_alert=True)


async def test_bak_callback_rejects_actor_without_permission(session):
    from bot.handlers.admin.audit import handle_bak_callback
    from bot.keyboards.admin.audit import BakCb

    logistic = await UserRepository(session).upsert(telegram_id=5, name="L", role=Role.LOGISTIC)
    await session.commit()

    callback = AsyncMock()
    callback.from_user.id = logistic.telegram_id
    await handle_bak_callback(callback, BakCb(a="recoverjobs"), session)
    callback.answer.assert_awaited_with("Недостаточно прав", show_alert=True)


async def test_aud_only_permission_cannot_reach_bak_callback(session):
    """Regression: audit.view и tasks.run_manual — независимые права,
    проверяемые раздельными константами (AUDIT_PERMISSION/BACKUP_PERMISSION)
    в общем модуле. Актор только с audit.view не должен пройти в BakCb."""
    from bot.database.repositories.permission_repository import PermissionRepository
    from bot.handlers.admin.audit import handle_bak_callback
    from bot.keyboards.admin.audit import BakCb

    partner = await UserRepository(session).upsert(telegram_id=6, name="P", role=Role.PARTNER)
    await PermissionRepository(session).set_permission(partner.id, "audit.view", True, None)
    await session.commit()

    callback = AsyncMock()
    callback.from_user.id = partner.telegram_id
    await handle_bak_callback(callback, BakCb(a="recoverjobs"), session)
    callback.answer.assert_awaited_with("Недостаточно прав", show_alert=True)


async def test_bak_only_permission_cannot_reach_aud_callback(session):
    """Обратный случай: только tasks.run_manual не должен пройти в AudCb."""
    from bot.database.repositories.permission_repository import PermissionRepository
    from bot.handlers.admin.audit import handle_aud_callback
    from bot.keyboards.admin.audit import AudCb

    partner = await UserRepository(session).upsert(telegram_id=7, name="P2", role=Role.PARTNER)
    await PermissionRepository(session).set_permission(partner.id, "tasks.run_manual", True, None)
    await session.commit()

    callback = AsyncMock()
    callback.from_user.id = partner.telegram_id
    await handle_aud_callback(callback, AudCb(a="page"), session)
    callback.answer.assert_awaited_with("Недостаточно прав", show_alert=True)


# ---------------------------------------------------------------------------
# Точки входа
# ---------------------------------------------------------------------------

async def test_entry_aud_shows_journal(session):
    from bot.handlers.admin.audit import handle_audit_section
    from bot.services.admin_service import AdminService
    from bot.keyboards.admin.main import AdminCb

    owner = await _owner(session)
    await AuditRepository(session).add(actor_user_id=owner.id, action="x.y")
    await session.commit()

    callback = AsyncMock()
    svc = AdminService(session)
    await handle_audit_section(callback, AdminCb(s="aud", a="open"), session, owner, svc)
    text = callback.message.edit_text.await_args.args[0]
    assert "Журнал изменений" in text and "x.y" in text


async def test_entry_aud_menu_action_shows_admin_menu(session):
    from bot.handlers.admin.audit import handle_audit_section
    from bot.services.admin_service import AdminService
    from bot.keyboards.admin.main import AdminCb

    owner = await _owner(session)
    await session.commit()

    callback = AsyncMock()
    svc = AdminService(session)
    await handle_audit_section(callback, AdminCb(s="aud", a="menu"), session, owner, svc)
    assert "Админ-панель" in callback.message.edit_text.await_args.args[0]


async def test_entry_bak_shows_pg_dump_hint(session):
    from bot.handlers.admin.audit import handle_backup_section
    from bot.services.admin_service import AdminService
    from bot.keyboards.admin.main import AdminCb

    owner = await _owner(session)
    await session.commit()

    callback = AsyncMock()
    svc = AdminService(session)
    await handle_backup_section(callback, AdminCb(s="bak", a="open"), session, owner, svc)
    text = callback.message.edit_text.await_args.args[0]
    assert "pg_dump" in text and "pg_restore" in text


async def test_entry_bak_menu_action_shows_admin_menu(session):
    from bot.handlers.admin.audit import handle_backup_section
    from bot.services.admin_service import AdminService
    from bot.keyboards.admin.main import AdminCb

    owner = await _owner(session)
    await session.commit()

    callback = AsyncMock()
    svc = AdminService(session)
    await handle_backup_section(callback, AdminCb(s="bak", a="menu"), session, owner, svc)
    assert "Админ-панель" in callback.message.edit_text.await_args.args[0]


# ---------------------------------------------------------------------------
# Пагинация / переключатель «мои»/«все»
# ---------------------------------------------------------------------------

async def test_toggle_switches_between_mine_and_all(session):
    from bot.handlers.admin.audit import handle_aud_callback
    from bot.keyboards.admin.audit import AudCb

    owner = await _owner(session)
    other = await UserRepository(session).upsert(telegram_id=2, name="P", role=Role.PARTNER)
    await AuditRepository(session).add(actor_user_id=owner.id, action="a.owner")
    await AuditRepository(session).add(actor_user_id=other.id, action="a.other")
    await session.commit()

    callback = AsyncMock()
    callback.from_user.id = owner.telegram_id
    await handle_aud_callback(callback, AudCb(a="toggle", mine=False), session)
    text = callback.message.edit_text.await_args.args[0]
    assert "a.owner" in text and "a.other" not in text
    kb = _reply_markup(callback)
    labels = [btn.text for row in kb.inline_keyboard for btn in row]
    assert "👥 Все действия" in labels


async def test_page_navigation_respects_mine_flag(session):
    from bot.handlers.admin.audit import handle_aud_callback
    from bot.keyboards.admin.audit import AudCb

    owner = await _owner(session)
    other = await UserRepository(session).upsert(telegram_id=2, name="P", role=Role.PARTNER)
    await AuditRepository(session).add(actor_user_id=owner.id, action="a.owner")
    await AuditRepository(session).add(actor_user_id=other.id, action="a.other")
    await session.commit()

    callback = AsyncMock()
    callback.from_user.id = owner.telegram_id
    await handle_aud_callback(callback, AudCb(a="page", p=1, mine=True), session)
    text = callback.message.edit_text.await_args.args[0]
    assert "a.owner" in text and "a.other" not in text


# ---------------------------------------------------------------------------
# «💾 Резервные операции» — confirm_token flows
# ---------------------------------------------------------------------------

async def test_recoverdeliv_confirm_flow(session):
    from bot.handlers.admin.audit import handle_bak_callback
    from bot.keyboards.admin.audit import BakCb
    from bot.keyboards.admin.confirm import ConfirmCb
    from bot.services.admin_service import AdminService

    owner = await _owner(session)
    await session.commit()

    callback = AsyncMock()
    callback.from_user.id = owner.telegram_id
    callback.bot = AsyncMock()
    await handle_bak_callback(callback, BakCb(a="recoverdeliv"), session)
    callback.bot.send_message.assert_not_awaited()

    kb = _reply_markup(callback)
    confirm_cb = ConfirmCb.unpack(kb.inline_keyboard[0][0].callback_data)
    svc = AdminService(session)
    entry = svc.get_pending(confirm_cb.t)
    assert entry is not None and entry.required_permission == "tasks.run_manual"

    ok = await svc.execute_confirmed(confirm_cb.t, session)
    await session.commit()
    assert ok is True
    logs = list(await session.scalars(select(AdminAuditLog)))
    assert any(l.action == "delivery.recover" for l in logs)


async def test_recoverjobs_confirm_flow(session_factory):
    from bot.handlers.admin.audit import handle_bak_callback
    from bot.keyboards.admin.audit import BakCb
    from bot.keyboards.admin.confirm import ConfirmCb
    from bot.services.admin_service import AdminService

    async with session_factory() as s:
        owner = await _owner(s)
        await s.commit()
        owner_id = owner.id

    scheduler = AsyncIOScheduler()
    scheduler.wb_service = SchedulerService(scheduler, AsyncMock(), session_factory)

    async with session_factory() as s:
        owner = await UserRepository(s).get_by_id(owner_id)
        callback = AsyncMock()
        callback.from_user.id = owner.telegram_id
        callback.bot = AsyncMock()
        await handle_bak_callback(
            callback, BakCb(a="recoverjobs"), s, dispatcher=FakeDispatcher(scheduler))

        kb = _reply_markup(callback)
        confirm_cb = ConfirmCb.unpack(kb.inline_keyboard[0][0].callback_data)
        svc = AdminService(s)
        # Коммит ДО execute_confirmed: recover_scheduler->recover_jobs открывает
        # СВОЮ сессию через session_factory (Task 12/13) — на StaticPool/SQLite
        # (общая физическая коннекция) это не может произойти, пока `s` держит
        # незакоммиченную транзакцию (тот же класс тестовой гигиены, что и в
        # test_toggle_auto_rebuilds_sync_job, Task 33).
        await s.commit()
        ok = await svc.execute_confirmed(confirm_cb.t, s)
        await s.commit()
        assert ok is True
        logs = list(await s.scalars(select(AdminAuditLog)))
        assert any(l.action == "scheduler.manual_recover" for l in logs)


async def test_recoverjobs_alerts_when_scheduler_unavailable(session):
    from bot.handlers.admin.audit import handle_bak_callback
    from bot.keyboards.admin.audit import BakCb

    owner = await _owner(session)
    await session.commit()

    callback = AsyncMock()
    callback.from_user.id = owner.telegram_id
    await handle_bak_callback(callback, BakCb(a="recoverjobs"), session, dispatcher=None)
    callback.answer.assert_awaited_with("Планировщик недоступен", show_alert=True)


# ---------------------------------------------------------------------------
# Regression: реальный .pack()/.unpack() round-trip
# ---------------------------------------------------------------------------

def test_audcb_default_fields_roundtrip():
    from bot.keyboards.admin.audit import AudCb

    cb = AudCb(a="noop")
    unpacked = AudCb.unpack(cb.pack())
    assert unpacked.a == "noop" and unpacked.p == 1 and unpacked.mine is False


def test_bakcb_roundtrip():
    from bot.keyboards.admin.audit import BakCb

    cb = BakCb(a="recoverjobs")
    unpacked = BakCb.unpack(cb.pack())
    assert unpacked.a == "recoverjobs"


def test_audit_page_keyboard_roundtrip():
    from bot.keyboards.admin.audit import AudCb, audit_page_keyboard
    from bot.keyboards.admin.main import AdminCb

    kb = audit_page_keyboard(page=2, mine_only=True)
    pager = kb.inline_keyboard[0]
    for btn in pager:
        AudCb.unpack(btn.callback_data)
    toggle = AudCb.unpack(kb.inline_keyboard[1][0].callback_data)
    assert toggle.a == "toggle" and toggle.mine is True
    back = AdminCb.unpack(kb.inline_keyboard[-1][0].callback_data)
    assert back.s == "aud" and back.a == "menu"


def test_backup_keyboard_roundtrip():
    from bot.keyboards.admin.audit import BakCb, backup_keyboard
    from bot.keyboards.admin.main import AdminCb

    kb = backup_keyboard()
    for row in kb.inline_keyboard[:-1]:
        for btn in row:
            BakCb.unpack(btn.callback_data)
    back = AdminCb.unpack(kb.inline_keyboard[-1][0].callback_data)
    assert back.s == "bak" and back.a == "menu"
