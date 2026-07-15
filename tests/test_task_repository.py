from datetime import date, datetime

from sqlalchemy import select

from bot.database.models import Role, TaskLog, TaskStatus
from bot.database.repositories.task_repository import TaskRepository
from bot.database.repositories.user_repository import UserRepository


async def make_instance(session, ext_id="t1"):
    user = await UserRepository(session).upsert(telegram_id=1, name="Валя", role=Role.MANAGER_WB)
    repo = TaskRepository(session)
    cfg = await repo.upsert_config(dict(
        external_task_id=ext_id, title="Проверить все артикулы",
        scenario="article_check", schedule_type="every_n_days", schedule_interval=2,
        responsible_user_id=user.id, need_approval=True, is_active=True))
    ts = datetime(2026, 7, 10, 9, 0)
    snapshot = dict(title_snapshot=cfg.title, need_approval_snapshot=True,
                    approval_timeout_hours_snapshot=24, article_batch_size_snapshot=15,
                    scenario_snapshot="article_check", settings_version=1,
                    responsible_name_snapshot="Валя")
    inst = await repo.create_instance_idempotent(cfg, ts, ts.replace(hour=12), snapshot)
    await session.commit()
    return repo, cfg, inst, user, ts, snapshot


async def test_idempotent_create(session):
    repo, cfg, inst, user, ts, snapshot = await make_instance(session)
    assert inst is not None and inst.schedule_key == f"{cfg.id}:{ts.isoformat()}"
    dup = await repo.create_instance_idempotent(cfg, ts, ts.replace(hour=12), snapshot)
    await session.commit()
    assert dup is None                       # UNIQUE(config_id, scheduled_at)


async def test_transition_happy_path_and_log(session):
    repo, _, inst, user, _, _ = await make_instance(session)
    got = await repo.transition_status(inst.id, [TaskStatus.CREATED],
                                       TaskStatus.IN_PROGRESS, user.id, "btn:start")
    await session.commit()
    assert got.status == TaskStatus.IN_PROGRESS
    log = (await session.execute(select(TaskLog))).scalar_one()
    assert (log.old_status, log.new_status) == (TaskStatus.CREATED, TaskStatus.IN_PROGRESS)


async def test_transition_rejects_wrong_state(session):
    """Двойное нажатие / гонка: второй переход из того же статуса не проходит."""
    repo, _, inst, user, _, _ = await make_instance(session)
    assert await repo.transition_status(inst.id, [TaskStatus.CREATED],
                                        TaskStatus.IN_PROGRESS, user.id, "btn:start")
    second = await repo.transition_status(inst.id, [TaskStatus.CREATED],
                                          TaskStatus.IN_PROGRESS, user.id, "btn:start")
    await session.commit()
    assert second is None                    # идемпотентный повторный callback
    logs = list(await session.scalars(select(TaskLog)))
    assert len(logs) == 1


async def test_auto_approve_only_from_waiting(session):
    repo, _, inst, user, _, _ = await make_instance(session)
    got = await repo.transition_status(inst.id, [TaskStatus.WAITING_APPROVAL],
                                       TaskStatus.AUTO_APPROVED, None, "auto:approve")
    assert got is None                       # задача в created — auto-approve не применяется


async def test_get_sent_unlogged_filters_status_and_flag(session):
    """Часть Б: для «Журнала отправок» выбираются только SENT-инстансы, ещё
    не выгруженные (sheet_logged_at IS NULL)."""
    from datetime import datetime
    from bot.database.models import DeliveryStatus
    from bot.database.repositories.task_repository import TaskRepository
    from bot.services.task_service import TaskService
    from tests.test_task_service import make_config

    cfg, _ = await make_config(session)
    svc = TaskService(session)
    sent = await svc.create_instance_for(cfg, datetime(2026, 7, 10, 9, 0))
    pending = await svc.create_instance_for(cfg, datetime(2026, 7, 11, 9, 0))
    logged = await svc.create_instance_for(cfg, datetime(2026, 7, 12, 9, 0))
    sent.delivery_status = DeliveryStatus.SENT
    sent.message_sent_at = datetime(2026, 7, 10, 9, 1)
    logged.delivery_status = DeliveryStatus.SENT
    logged.message_sent_at = datetime(2026, 7, 12, 9, 1)
    logged.sheet_logged_at = datetime(2026, 7, 12, 10, 0)   # уже выгружен
    await session.commit()

    rows = await TaskRepository(session).get_sent_unlogged()
    assert [r.id for r in rows] == [sent.id]                # pending и logged — мимо


async def test_get_unnotified_status_logs_filters_status_and_flag(session):
    """Часть Г: для уведомлений выбираются только записи с new_status из
    переданного списка, по которым уведомление ещё не отправлено
    (owner_notified_at IS NULL)."""
    repo, cfg, inst, user, ts, snapshot = await make_instance(session)
    fresh = TaskLog(task_instance_id=inst.id, user_id=user.id, action="task.take",
                    old_status="created", new_status="in_progress")
    done = TaskLog(task_instance_id=inst.id, user_id=None, action="auto:overdue",
                   old_status="in_progress", new_status="overdue",
                   owner_notified_at=datetime(2026, 7, 10, 10, 0))   # уже уведомлён
    other = TaskLog(task_instance_id=inst.id, user_id=user.id, action="task.done",
                    old_status="in_progress", new_status="waiting_approval")
    session.add_all([fresh, done, other])
    await session.commit()

    rows = await repo.get_unnotified_status_logs(
        ["in_progress", "completed", "problem", "overdue"])
    assert [r.id for r in rows] == [fresh.id]              # done и other — мимо


async def test_get_unlogged_status_logs_no_status_filter(session):
    """Часть Д: для «Истории статусов» выбираются ВСЕ записи TaskLog (без
    фильтра по статусу) с sheet_logged_at IS NULL — независимо от флага
    owner_notified_at Части Г (два потребителя, две независимые пометки)."""
    repo, cfg, inst, user, ts, snapshot = await make_instance(session)
    any_status = TaskLog(task_instance_id=inst.id, user_id=user.id, action="task.done",
                         old_status="in_progress", new_status="waiting_approval")
    notified = TaskLog(task_instance_id=inst.id, user_id=None, action="auto:overdue",
                       old_status="created", new_status="overdue",
                       owner_notified_at=datetime(2026, 7, 11, 9, 0))  # флаг Г не мешает
    logged = TaskLog(task_instance_id=inst.id, user_id=user.id, action="task.take",
                     old_status="created", new_status="in_progress",
                     sheet_logged_at=datetime(2026, 7, 10, 10, 0))     # уже выгружена
    session.add_all([any_status, notified, logged])
    await session.commit()

    rows = await repo.get_unlogged_status_logs()
    assert [r.id for r in rows] == [any_status.id, notified.id]


async def test_get_pending_rebuild_returns_only_flagged_configs(session):
    repo = TaskRepository(session)
    flagged = await repo.upsert_config(dict(
        external_task_id="flagged", title="Flagged", schedule_type="daily",
        is_active=True))
    flagged.pending_rebuild = True
    not_flagged = await repo.upsert_config(dict(
        external_task_id="not_flagged", title="Not flagged", schedule_type="daily",
        is_active=True))
    await session.commit()

    result = await repo.get_pending_rebuild()
    result_ids = {c.id for c in result}
    assert flagged.id in result_ids
    assert not_flagged.id not in result_ids
