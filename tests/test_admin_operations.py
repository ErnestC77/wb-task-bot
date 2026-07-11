from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select

from bot.database.models import AdminAuditLog, DeliveryStatus, Role, TaskInstance, TaskStatus
from bot.database.repositories.task_repository import TaskRepository
from bot.database.repositories.user_repository import UserRepository
from bot.services.scheduler_service import SchedulerService
from bot.services.task_service import TaskService
from tests.test_task_service import make_config


def _bot():
    bot = AsyncMock()
    bot.send_message.return_value = SimpleNamespace(message_id=1, chat=SimpleNamespace(id=-1))
    return bot


def _reply_markup(callback):
    return callback.message.edit_text.await_args.kwargs.get("reply_markup") \
        or callback.message.edit_text.await_args.args[1]


async def _owner(session):
    return await UserRepository(session).upsert(telegram_id=1, name="O", role=Role.OWNER)


# ---------------------------------------------------------------------------
# Step 1 (брифовые тесты, дословно) — тест 28 + force_close/manual_auto_approve/recalc
# ---------------------------------------------------------------------------

async def test_resend_increments_delivery_attempt(session):       # тест 28
    from bot.handlers.admin.operations import resend_task_message
    cfg, valya = await make_config(session)
    owner = await UserRepository(session).upsert(telegram_id=1, name="O", role=Role.OWNER)
    inst = await TaskService(session).create_instance_for(cfg, datetime(2026, 7, 10, 9))
    bot = AsyncMock()
    bot.send_message.return_value = SimpleNamespace(message_id=1,
                                                     chat=SimpleNamespace(id=-1))
    await resend_task_message(session, bot, owner, inst.id)
    await resend_task_message(session, bot, owner, inst.id)
    await session.commit()
    assert inst.delivery_attempts == 2
    logs = list(await session.scalars(select(AdminAuditLog)))
    assert sum(1 for l in logs if l.action == "task.resend_message") == 2


async def test_force_close_from_any_open_status(session):
    cfg, valya = await make_config(session)
    owner = await UserRepository(session).upsert(telegram_id=1, name="O", role=Role.OWNER)
    inst = await TaskService(session).create_instance_for(cfg, datetime(2026, 7, 10, 9))
    await session.commit()
    from bot.handlers.admin.operations import force_close
    got = await force_close(session, owner, inst.id, TaskStatus.CANCELLED)
    await session.commit()
    assert got.status == TaskStatus.CANCELLED


async def test_manual_auto_approve_only_from_waiting(session):
    cfg, valya = await make_config(session)
    owner = await UserRepository(session).upsert(telegram_id=1, name="O", role=Role.OWNER)
    inst = await TaskService(session).create_instance_for(cfg, datetime(2026, 7, 10, 9))
    await TaskRepository(session).transition_status(
        inst.id, [TaskStatus.CREATED], TaskStatus.IN_PROGRESS, valya.id, "x")
    from bot.handlers.admin.operations import manual_auto_approve
    bot = AsyncMock()
    got = await manual_auto_approve(session, bot, owner, inst.id)
    assert got is None                                 # ещё in_progress — не применяется
    await TaskRepository(session).transition_status(
        inst.id, [TaskStatus.IN_PROGRESS], TaskStatus.WAITING_APPROVAL, valya.id, "x")
    got = await manual_auto_approve(session, bot, owner, inst.id)
    await session.commit()
    assert got.status == TaskStatus.AUTO_APPROVED


async def test_recalc_next_run_reschedules_job(session_factory):
    async with session_factory() as s:
        user = await UserRepository(s).upsert(telegram_id=1, name="V", role=Role.MANAGER_WB)
        owner = await UserRepository(s).upsert(telegram_id=2, name="O", role=Role.OWNER)
        cfg = await TaskRepository(s).upsert_config(dict(
            external_task_id="t", title="t", schedule_type="every_n_days",
            schedule_interval=2, responsible_user_id=user.id, is_active=True,
            next_run_at=datetime(2020, 1, 1)))   # устаревшее значение
        await s.commit()
        cfg_id, owner_id = cfg.id, owner.id
    scheduler = AsyncIOScheduler()
    svc = SchedulerService(scheduler, AsyncMock(), session_factory)
    from bot.handlers.admin.operations import recalc_next_run
    async with session_factory() as s:
        owner = await UserRepository(s).get_by_id(owner_id)
        await recalc_next_run(s, owner, cfg_id, svc)
        await s.commit()
        cfg = await TaskRepository(s).get_config(cfg_id)
        assert cfg.next_run_at > datetime(2026, 1, 1)
    assert scheduler.get_job(f"config:{cfg_id}") is not None


# ---------------------------------------------------------------------------
# force_close — дополнительные ветки
# ---------------------------------------------------------------------------

async def test_force_close_rejects_bad_target_status(session):
    from bot.handlers.admin.operations import force_close
    cfg, valya = await make_config(session)
    owner = await _owner(session)
    inst = await TaskService(session).create_instance_for(cfg, datetime(2026, 7, 10, 9))
    await session.commit()
    import pytest
    with pytest.raises(ValueError):
        await force_close(session, owner, inst.id, TaskStatus.IN_PROGRESS)


async def test_force_close_sets_completed_at_and_cancelled_at(session):
    """Regression: брифовый код не проставлял completed_at/cancelled_at при
    принудительном закрытии — рассинхронизация с обычным путём (approval_service/
    article_check_service всегда передают эти timestamp'ы). Исправлено."""
    from bot.handlers.admin.operations import force_close
    cfg, valya = await make_config(session)
    owner = await _owner(session)
    inst1 = await TaskService(session).create_instance_for(cfg, datetime(2026, 7, 10, 9))
    inst2 = await TaskService(session).create_instance_for(cfg, datetime(2026, 7, 11, 9))
    await session.commit()
    got1 = await force_close(session, owner, inst1.id, TaskStatus.COMPLETED)
    got2 = await force_close(session, owner, inst2.id, TaskStatus.CANCELLED)
    await session.commit()
    assert got1.completed_at is not None
    assert got2.cancelled_at is not None


async def test_force_close_returns_none_for_unknown_instance(session):
    from bot.handlers.admin.operations import force_close
    owner = await _owner(session)
    await session.commit()
    assert await force_close(session, owner, 999999, TaskStatus.CANCELLED) is None


async def test_force_close_works_from_overdue(session):
    from bot.handlers.admin.operations import force_close
    cfg, valya = await make_config(session)
    owner = await _owner(session)
    inst = await TaskService(session).create_instance_for(cfg, datetime(2026, 7, 10, 9))
    await TaskRepository(session).transition_status(
        inst.id, [TaskStatus.CREATED], TaskStatus.OVERDUE, None, "auto:overdue")
    await session.commit()
    got = await force_close(session, owner, inst.id, TaskStatus.COMPLETED)
    await session.commit()
    assert got.status == TaskStatus.COMPLETED


# ---------------------------------------------------------------------------
# resend_task_message — не дублирует уже-SENT задачу молча
# ---------------------------------------------------------------------------

async def test_resend_rejects_unknown_instance(session):
    from bot.handlers.admin.operations import resend_task_message
    owner = await _owner(session)
    await session.commit()
    import pytest
    with pytest.raises(ValueError):
        await resend_task_message(session, _bot(), owner, 999999)


# ---------------------------------------------------------------------------
# manual_run_config
# ---------------------------------------------------------------------------

async def test_manual_run_config_creates_instance_and_sends(session):
    from bot.handlers.admin.operations import manual_run_config
    cfg, valya = await make_config(session)
    owner = await _owner(session)
    await session.commit()
    ok = await manual_run_config(session, _bot(), owner, cfg.id, at=datetime(2026, 7, 12, 10))
    await session.commit()
    assert ok is True
    inst = await session.scalar(select(TaskInstance))
    assert inst is not None and inst.delivery_status == DeliveryStatus.SENT


async def test_manual_run_config_rejects_unknown_config(session):
    from bot.handlers.admin.operations import manual_run_config
    owner = await _owner(session)
    await session.commit()
    import pytest
    with pytest.raises(ValueError):
        await manual_run_config(session, _bot(), owner, 999999)


async def test_manual_run_config_returns_false_on_duplicate(session):
    from bot.handlers.admin.operations import manual_run_config
    cfg, valya = await make_config(session)
    owner = await _owner(session)
    await session.commit()
    at = datetime(2026, 7, 12, 10)
    assert await manual_run_config(session, _bot(), owner, cfg.id, at=at) is True
    assert await manual_run_config(session, _bot(), owner, cfg.id, at=at) is False


# ---------------------------------------------------------------------------
# resend_approval_request / reregister_reminders
# ---------------------------------------------------------------------------

async def test_resend_approval_request_only_from_waiting(session):
    from bot.handlers.admin.operations import resend_approval_request
    cfg, valya = await make_config(session)
    owner = await _owner(session)
    inst = await TaskService(session).create_instance_for(cfg, datetime(2026, 7, 10, 9))
    await session.commit()
    import pytest
    with pytest.raises(ValueError):
        await resend_approval_request(session, _bot(), owner, inst.id)


async def test_reregister_reminders_only_from_open(session):
    from bot.handlers.admin.operations import reregister_reminders
    cfg, valya = await make_config(session)
    owner = await _owner(session)
    inst = await TaskService(session).create_instance_for(cfg, datetime(2026, 7, 10, 9))
    await TaskRepository(session).transition_status(
        inst.id, [TaskStatus.CREATED], TaskStatus.COMPLETED, valya.id, "x")
    await session.commit()
    import pytest
    with pytest.raises(ValueError):
        await reregister_reminders(session, owner, inst.id, None)


# ---------------------------------------------------------------------------
# recover_pending_deliveries / test_topic_send / test_private_send
# ---------------------------------------------------------------------------

async def test_recover_pending_deliveries_sends_and_counts(session):
    from bot.handlers.admin.operations import recover_pending_deliveries
    cfg, valya = await make_config(session)
    owner = await _owner(session)
    await TaskService(session).create_instance_for(cfg, datetime(2026, 7, 10, 9))
    await session.commit()
    count = await recover_pending_deliveries(session, _bot(), owner)
    await session.commit()
    assert count == 1


async def test_recover_scheduler_logs_audit_with_counters(session_factory):
    from bot.handlers.admin.operations import recover_scheduler
    from apscheduler.schedulers.asyncio import AsyncIOScheduler

    async with session_factory() as s:
        owner = await _owner(s)
        await s.commit()
        owner_id = owner.id

    scheduler = AsyncIOScheduler()
    counters = await recover_scheduler(scheduler, _bot(), session_factory, owner_id)
    assert isinstance(counters, dict) and "configs" in counters

    async with session_factory() as s:
        logs = list(await s.scalars(select(AdminAuditLog)))
        entry = next(l for l in logs if l.action == "scheduler.manual_recover")
        assert entry.actor_user_id == owner_id


async def test_test_topic_send_logs_audit_result(session):
    from bot.handlers.admin.operations import test_topic_send
    owner = await _owner(session)
    await session.commit()
    ok = await test_topic_send(session, _bot(), owner, "reminders")
    await session.commit()
    assert ok is True
    logs = list(await session.scalars(select(AdminAuditLog)))
    entry = next(l for l in logs if l.action == "topic.test_send")
    assert entry.entity_id == "reminders" and entry.result == "ok"


async def test_test_private_send_updates_private_chat_available(session):
    from bot.handlers.admin.operations import test_private_send
    owner = await _owner(session)
    target = await UserRepository(session).upsert(telegram_id=2, name="P", role=Role.PARTNER)
    await session.commit()
    ok = await test_private_send(session, _bot(), owner, target.id)
    await session.commit()
    assert ok is True
    assert target.private_chat_available is True


async def test_test_private_send_rejects_unknown_user(session):
    from bot.handlers.admin.operations import test_private_send
    owner = await _owner(session)
    await session.commit()
    import pytest
    with pytest.raises(ValueError):
        await test_private_send(session, _bot(), owner, 999999)


# ---------------------------------------------------------------------------
# actor-check (callback-слой)
# ---------------------------------------------------------------------------

async def test_ops_callback_rejects_actor_without_permission(session):
    from bot.handlers.admin.operations import handle_ops_callback
    from bot.keyboards.admin.operations import OpsCb

    logistic = await UserRepository(session).upsert(telegram_id=5, name="L", role=Role.LOGISTIC)
    await session.commit()

    callback = AsyncMock()
    callback.from_user.id = logistic.telegram_id
    await handle_ops_callback(callback, OpsCb(a="actlist"), session)
    callback.answer.assert_awaited_with("Недостаточно прав", show_alert=True)


async def test_ops_callback_rejects_unregistered_user(session):
    from bot.handlers.admin.operations import handle_ops_callback
    from bot.keyboards.admin.operations import OpsCb

    callback = AsyncMock()
    callback.from_user.id = 999999
    await handle_ops_callback(callback, OpsCb(a="actlist"), session)
    callback.answer.assert_awaited_with("Недостаточно прав", show_alert=True)


# ---------------------------------------------------------------------------
# Точки входа
# ---------------------------------------------------------------------------

async def test_entry_run_shows_run_list(session):
    from bot.handlers.admin.operations import handle_operations_section
    from bot.services.admin_service import AdminService
    from bot.keyboards.admin.main import AdminCb

    cfg, valya = await make_config(session)
    owner = await _owner(session)
    await session.commit()

    callback = AsyncMock()
    svc = AdminService(session)
    await handle_operations_section(callback, AdminCb(s="run", a="open"), session, owner, svc)
    text = callback.message.edit_text.await_args.args[0]
    assert "Ручной запуск" in text


async def test_entry_act_shows_active_list(session):
    from bot.handlers.admin.operations import handle_operations_section
    from bot.services.admin_service import AdminService
    from bot.keyboards.admin.main import AdminCb

    owner = await _owner(session)
    await session.commit()

    callback = AsyncMock()
    svc = AdminService(session)
    await handle_operations_section(callback, AdminCb(s="act", a="open"), session, owner, svc)
    text = callback.message.edit_text.await_args.args[0]
    assert "Активных задач нет" in text


async def test_entry_bak_not_yet_implemented(session):
    from bot.handlers.admin.operations import handle_operations_section
    from bot.services.admin_service import AdminService
    from bot.keyboards.admin.main import AdminCb

    owner = await _owner(session)
    await session.commit()

    callback = AsyncMock()
    svc = AdminService(session)
    await handle_operations_section(callback, AdminCb(s="bak", a="open"), session, owner, svc)
    callback.answer.assert_awaited_with("Раздел в разработке")


async def test_entry_menu_action_shows_admin_menu(session):
    from bot.handlers.admin.operations import handle_operations_section
    from bot.services.admin_service import AdminService
    from bot.keyboards.admin.main import AdminCb

    owner = await _owner(session)
    await session.commit()

    callback = AsyncMock()
    svc = AdminService(session)
    await handle_operations_section(callback, AdminCb(s="run", a="menu"), session, owner, svc)
    assert "Админ-панель" in callback.message.edit_text.await_args.args[0]


# ---------------------------------------------------------------------------
# Список / карточка активных задач
# ---------------------------------------------------------------------------

async def test_active_list_shows_open_and_waiting_approval_not_completed(session):
    from bot.handlers.admin.operations import handle_ops_callback
    from bot.keyboards.admin.operations import OpsCb

    cfg, valya = await make_config(session)
    owner = await _owner(session)
    inst_open = await TaskService(session).create_instance_for(cfg, datetime(2026, 7, 10, 9))
    inst_wait = await TaskService(session).create_instance_for(cfg, datetime(2026, 7, 11, 9))
    inst_done = await TaskService(session).create_instance_for(cfg, datetime(2026, 7, 12, 9))
    await TaskRepository(session).transition_status(
        inst_wait.id, [TaskStatus.CREATED], TaskStatus.WAITING_APPROVAL, valya.id, "x")
    await TaskRepository(session).transition_status(
        inst_done.id, [TaskStatus.CREATED], TaskStatus.COMPLETED, valya.id, "x")
    await session.commit()

    callback = AsyncMock()
    callback.from_user.id = owner.telegram_id
    await handle_ops_callback(callback, OpsCb(a="actlist"), session)
    kb = _reply_markup(callback)
    # первая колонка — id инстанса, зашитый в callback_data кнопки строки
    row_ids = {OpsCb.unpack(row[0].callback_data).id for row in kb.inline_keyboard
              if row[0].callback_data.startswith("o:card")}
    assert row_ids == {inst_open.id, inst_wait.id}
    labels = " ".join(btn.text for row in kb.inline_keyboard for btn in row)
    assert "waiting_approval" in labels


async def test_show_card_includes_waiting_approval_buttons_only_when_waiting(session):
    from bot.handlers.admin.operations import handle_ops_callback
    from bot.keyboards.admin.operations import OpsCb

    cfg, valya = await make_config(session)
    owner = await _owner(session)
    inst = await TaskService(session).create_instance_for(cfg, datetime(2026, 7, 10, 9))
    await session.commit()

    callback = AsyncMock()
    callback.from_user.id = owner.telegram_id
    await handle_ops_callback(callback, OpsCb(a="card", id=inst.id), session)
    kb = _reply_markup(callback)
    labels = [btn.text for row in kb.inline_keyboard for btn in row]
    assert "📨 Повторить запрос подтверждения" not in labels
    assert "⚡ Подтвердить автоматически сейчас" not in labels

    await TaskRepository(session).transition_status(
        inst.id, [TaskStatus.CREATED], TaskStatus.WAITING_APPROVAL, valya.id, "x")
    await session.commit()
    await handle_ops_callback(callback, OpsCb(a="card", id=inst.id), session)
    kb2 = _reply_markup(callback)
    labels2 = [btn.text for row in kb2.inline_keyboard for btn in row]
    assert "📨 Повторить запрос подтверждения" in labels2
    assert "⚡ Подтвердить автоматически сейчас" in labels2


async def test_show_card_rejects_unknown_instance(session):
    from bot.handlers.admin.operations import handle_ops_callback
    from bot.keyboards.admin.operations import OpsCb

    owner = await _owner(session)
    await session.commit()

    callback = AsyncMock()
    callback.from_user.id = owner.telegram_id
    await handle_ops_callback(callback, OpsCb(a="card", id=999999), session)
    callback.answer.assert_awaited_with("Задача не найдена", show_alert=True)


async def test_card_reassign_button_uses_task25_userscb(session):
    from bot.handlers.admin.operations import handle_ops_callback
    from bot.keyboards.admin.operations import OpsCb
    from bot.keyboards.admin.users import UsrCb

    cfg, valya = await make_config(session)
    owner = await _owner(session)
    inst = await TaskService(session).create_instance_for(cfg, datetime(2026, 7, 10, 9))
    await session.commit()

    callback = AsyncMock()
    callback.from_user.id = owner.telegram_id
    await handle_ops_callback(callback, OpsCb(a="card", id=inst.id), session)
    kb = _reply_markup(callback)
    reassign_buttons = [btn for row in kb.inline_keyboard for btn in row
                        if btn.text == "🔁 Переназначить"]
    assert len(reassign_buttons) == 1
    cb = UsrCb.unpack(reassign_buttons[0].callback_data)
    assert cb.a == "reassign_pick" and cb.id == inst.id and cb.k == str(valya.id)


# ---------------------------------------------------------------------------
# confirm_token flows — dangerous ops
# ---------------------------------------------------------------------------

async def test_runpick_goes_through_confirm_token_and_creates_instance(session):
    from bot.handlers.admin.operations import handle_ops_callback
    from bot.keyboards.admin.confirm import ConfirmCb
    from bot.keyboards.admin.operations import OpsCb
    from bot.services.admin_service import AdminService

    cfg, valya = await make_config(session)
    owner = await _owner(session)
    await session.commit()

    callback = AsyncMock()
    callback.from_user.id = owner.telegram_id
    callback.bot = _bot()
    await handle_ops_callback(callback, OpsCb(a="runpick", id=cfg.id), session)
    callback.bot.send_message.assert_not_awaited()

    kb = _reply_markup(callback)
    confirm_cb = ConfirmCb.unpack(kb.inline_keyboard[0][0].callback_data)
    svc = AdminService(session)
    entry = svc.get_pending(confirm_cb.t)
    assert entry is not None and entry.required_permission == "tasks.run_manual"

    ok = await svc.execute_confirmed(confirm_cb.t, session)
    await session.commit()
    assert ok is True
    callback.bot.send_message.assert_awaited()


async def test_resend_confirm_flow(session):
    from bot.handlers.admin.operations import handle_ops_callback
    from bot.keyboards.admin.confirm import ConfirmCb
    from bot.keyboards.admin.operations import OpsCb
    from bot.services.admin_service import AdminService

    cfg, valya = await make_config(session)
    owner = await _owner(session)
    inst = await TaskService(session).create_instance_for(cfg, datetime(2026, 7, 10, 9))
    await session.commit()

    callback = AsyncMock()
    callback.from_user.id = owner.telegram_id
    callback.bot = _bot()
    await handle_ops_callback(callback, OpsCb(a="resend", id=inst.id), session)

    kb = _reply_markup(callback)
    confirm_cb = ConfirmCb.unpack(kb.inline_keyboard[0][0].callback_data)
    svc = AdminService(session)
    ok = await svc.execute_confirmed(confirm_cb.t, session)
    await session.commit()
    assert ok is True
    assert inst.delivery_attempts == 1


async def test_close_done_confirm_flow(session):
    from bot.handlers.admin.operations import handle_ops_callback
    from bot.keyboards.admin.confirm import ConfirmCb
    from bot.keyboards.admin.operations import OpsCb
    from bot.services.admin_service import AdminService

    cfg, valya = await make_config(session)
    owner = await _owner(session)
    inst = await TaskService(session).create_instance_for(cfg, datetime(2026, 7, 10, 9))
    await session.commit()

    callback = AsyncMock()
    callback.from_user.id = owner.telegram_id
    await handle_ops_callback(callback, OpsCb(a="close_done", id=inst.id), session)

    kb = _reply_markup(callback)
    confirm_cb = ConfirmCb.unpack(kb.inline_keyboard[0][0].callback_data)
    svc = AdminService(session)
    ok = await svc.execute_confirmed(confirm_cb.t, session)
    await session.commit()
    assert ok is True
    assert inst.status == TaskStatus.COMPLETED


async def test_resendappr_rejects_when_not_waiting(session):
    from bot.handlers.admin.operations import handle_ops_callback
    from bot.keyboards.admin.operations import OpsCb

    cfg, valya = await make_config(session)
    owner = await _owner(session)
    inst = await TaskService(session).create_instance_for(cfg, datetime(2026, 7, 10, 9))
    await session.commit()

    callback = AsyncMock()
    callback.from_user.id = owner.telegram_id
    await handle_ops_callback(callback, OpsCb(a="resendappr", id=inst.id), session)
    callback.answer.assert_awaited_with("Задача не ожидает подтверждения", show_alert=True)


async def test_autoapprove_confirm_flow(session):
    from bot.handlers.admin.operations import handle_ops_callback
    from bot.keyboards.admin.confirm import ConfirmCb
    from bot.keyboards.admin.operations import OpsCb
    from bot.services.admin_service import AdminService

    cfg, valya = await make_config(session)
    owner = await _owner(session)
    inst = await TaskService(session).create_instance_for(cfg, datetime(2026, 7, 10, 9))
    await TaskRepository(session).transition_status(
        inst.id, [TaskStatus.CREATED], TaskStatus.WAITING_APPROVAL, valya.id, "x")
    await session.commit()

    callback = AsyncMock()
    callback.from_user.id = owner.telegram_id
    callback.bot = _bot()
    await handle_ops_callback(callback, OpsCb(a="autoapprove", id=inst.id), session)

    kb = _reply_markup(callback)
    confirm_cb = ConfirmCb.unpack(kb.inline_keyboard[0][0].callback_data)
    svc = AdminService(session)
    ok = await svc.execute_confirmed(confirm_cb.t, session)
    await session.commit()
    assert ok is True
    assert inst.status == TaskStatus.AUTO_APPROVED


# ---------------------------------------------------------------------------
# Regression: реальный .pack()/.unpack() round-trip
# ---------------------------------------------------------------------------

def test_opscb_default_fields_roundtrip():
    from bot.keyboards.admin.operations import OpsCb

    cb = OpsCb(a="noop")
    unpacked = OpsCb.unpack(cb.pack())
    assert unpacked.a == "noop" and unpacked.id == 0 and unpacked.p == 1


def test_run_list_keyboard_roundtrip():
    from bot.keyboards.admin.operations import OpsCb, run_list_keyboard
    from bot.keyboards.admin.main import AdminCb

    entries = [(1, "a"), (2, "b")]
    kb = run_list_keyboard(entries, page=1, total_pages=2)
    for row in kb.inline_keyboard[:2]:
        cb = OpsCb.unpack(row[0].callback_data)
        assert cb.a == "runpick"
    pager = kb.inline_keyboard[2]
    for btn in pager:
        OpsCb.unpack(btn.callback_data)
    back = AdminCb.unpack(kb.inline_keyboard[-1][0].callback_data)
    assert back.s == "run" and back.a == "menu"


def test_active_list_keyboard_roundtrip():
    from bot.keyboards.admin.operations import OpsCb, active_list_keyboard
    from bot.keyboards.admin.main import AdminCb

    entries = [(1, "a"), (2, "b")]
    kb = active_list_keyboard(entries, page=1, total_pages=2)
    for row in kb.inline_keyboard[:2]:
        cb = OpsCb.unpack(row[0].callback_data)
        assert cb.a == "card"
    back = AdminCb.unpack(kb.inline_keyboard[-1][0].callback_data)
    assert back.s == "act" and back.a == "menu"


def test_instance_card_keyboard_roundtrip_with_and_without_waiting():
    from bot.keyboards.admin.operations import OpsCb, instance_card_keyboard
    from bot.keyboards.admin.users import UsrCb

    kb = instance_card_keyboard(5, 7, waiting_approval=True)
    for row in kb.inline_keyboard:
        for btn in row:
            if btn.callback_data.startswith("o:"):
                OpsCb.unpack(btn.callback_data)
            else:
                UsrCb.unpack(btn.callback_data)
    labels = [btn.text for row in kb.inline_keyboard for btn in row]
    assert "📨 Повторить запрос подтверждения" in labels

    kb2 = instance_card_keyboard(5, None, waiting_approval=False)
    labels2 = [btn.text for row in kb2.inline_keyboard for btn in row]
    assert "🔁 Переназначить" not in labels2
    assert "📨 Повторить запрос подтверждения" not in labels2
