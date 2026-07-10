from datetime import datetime, timedelta
from unittest.mock import AsyncMock

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from bot.database.models import DeliveryStatus, Role, TaskStatus
from bot.database.repositories.task_repository import TaskRepository
from bot.database.repositories.user_repository import UserRepository
from bot.services.scheduler_recovery_service import recover_jobs


async def seed(session_factory):
    async with session_factory() as s:
        user = await UserRepository(s).upsert(telegram_id=1, name="Валя", role=Role.MANAGER_WB)
        repo = TaskRepository(s)
        cfg = await repo.upsert_config(dict(
            external_task_id="articles_check_all", title="Проверка",
            scenario="article_check", schedule_type="every_n_days", schedule_interval=2,
            responsible_user_id=user.id, is_active=True,
            next_run_at=datetime.utcnow() + timedelta(days=1)))
        inst = await repo.create_instance_idempotent(
            cfg, datetime.utcnow() - timedelta(hours=1), None,
            dict(title_snapshot="Проверка", scenario_snapshot="article_check",
                 remind_after_hours_snapshot=3, second_remind_after_hours_snapshot=6,
                 need_approval_snapshot=True, approval_timeout_hours_snapshot=24))
        # задача, зависшая в waiting_approval с дедлайном в будущем
        waiting = await repo.create_instance_idempotent(
            cfg, datetime.utcnow() - timedelta(hours=2), None,
            dict(title_snapshot="Проверка", scenario_snapshot="article_check",
                 need_approval_snapshot=True, approval_timeout_hours_snapshot=24))
        await repo.transition_status(waiting.id,
                                     [TaskStatus.CREATED], TaskStatus.IN_PROGRESS, None, "x")
        await repo.transition_status(waiting.id,
                                     [TaskStatus.IN_PROGRESS], TaskStatus.WAITING_APPROVAL,
                                     None, "x",
                                     approval_deadline_at=datetime.utcnow() + timedelta(hours=20))
        # зависшая доставка
        inst.delivery_status = DeliveryStatus.RETRYING
        inst.next_retry_at = datetime.utcnow() - timedelta(minutes=5)
        await s.commit()
        return cfg.id, inst.id, waiting.id


async def test_recover_registers_all_jobs(session_factory):
    cfg_id, inst_id, waiting_id = await seed(session_factory)
    scheduler = AsyncIOScheduler()
    counters = await recover_jobs(scheduler, AsyncMock(), session_factory)
    job_ids = {j.id for j in scheduler.get_jobs()}
    assert f"config:{cfg_id}" in job_ids
    assert f"remind1:{inst_id}" in job_ids and f"remind2:{inst_id}" in job_ids
    assert f"overdue:{inst_id}" in job_ids
    assert f"auto_approve:{waiting_id}" in job_ids
    assert f"retry_delivery:{inst_id}:recover" in job_ids
    assert counters["configs"] >= 1 and counters["auto_approve"] == 1


async def test_recover_is_idempotent(session_factory):
    """Повторный recover не создаёт дублей (replace_existing)."""
    await seed(session_factory)
    scheduler = AsyncIOScheduler()
    await recover_jobs(scheduler, AsyncMock(), session_factory)
    first = len(scheduler.get_jobs())
    await recover_jobs(scheduler, AsyncMock(), session_factory)
    assert len(scheduler.get_jobs()) == first
