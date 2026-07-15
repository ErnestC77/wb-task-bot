"""Task 38 Step 5: те же сценарии восстановления, что и в unit-тестах Task 13
(`tests/test_scheduler_recovery.py`), но на реальном PostgreSQL — проверяет,
что сравнение `TIMESTAMP` (без TZ, см. модели: `DateTime` без `timezone=True`)
с наивным `datetime.utcnow()` в WHERE-условиях `recover_jobs` действительно
находит "просроченные"/"зависшие" записи на реальном диалекте (в SQLite типы
не проверяются вообще, там любое сравнение "работает" независимо от типов)."""
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from bot.database.models import DeliveryStatus, Role, TaskStatus
from bot.database.repositories.task_repository import TaskRepository
from bot.database.repositories.user_repository import UserRepository
from bot.services.scheduler_recovery_service import recover_jobs

pytestmark = pytest.mark.pg


async def seed(pg_session_factory):
    async with pg_session_factory() as s:
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
        inst.delivery_status = DeliveryStatus.RETRYING
        inst.next_retry_at = datetime.utcnow() - timedelta(minutes=5)
        await s.commit()
        return cfg.id, inst.id, waiting.id


async def test_recover_registers_all_jobs_on_postgres(pg_session_factory):
    cfg_id, inst_id, waiting_id = await seed(pg_session_factory)
    scheduler = AsyncIOScheduler()
    counters = await recover_jobs(scheduler, AsyncMock(), pg_session_factory)
    job_ids = {j.id for j in scheduler.get_jobs()}
    assert f"config:{cfg_id}" in job_ids
    assert f"not_taken:{inst_id}" in job_ids
    assert f"remind1:{inst_id}" not in job_ids and f"remind2:{inst_id}" not in job_ids
    assert f"overdue:{inst_id}" not in job_ids
    assert f"auto_approve:{waiting_id}" in job_ids
    assert f"retry_delivery:{inst_id}:recover" in job_ids
    assert counters["configs"] >= 1 and counters["auto_approve"] == 1


async def test_recover_pending_delivery_actually_sends_message_on_postgres(pg_session_factory):
    """Регрессия Task 13, портированная на реальный Postgres: PENDING-доставка
    реально восстанавливается и отправляется (не остаётся тихим no-op)."""
    async with pg_session_factory() as s:
        user = await UserRepository(s).upsert(telegram_id=2, name="Марат", role=Role.MANAGER_WB)
        repo = TaskRepository(s)
        cfg = await repo.upsert_config(dict(
            external_task_id="articles_check_pending", title="Проверка",
            scenario="article_check", schedule_type="every_n_days", schedule_interval=2,
            responsible_user_id=user.id, is_active=True,
            next_run_at=datetime.utcnow() + timedelta(days=1)))
        inst = await repo.create_instance_idempotent(
            cfg, datetime.utcnow() - timedelta(hours=1), None,
            dict(title_snapshot="Проверка", scenario_snapshot="article_check"))
        await s.commit()
        assert inst.delivery_status == DeliveryStatus.PENDING
        inst_id = inst.id

    scheduler = AsyncIOScheduler()
    bot = AsyncMock()
    bot.send_message.return_value = SimpleNamespace(message_id=555, chat=SimpleNamespace(id=-100))

    await recover_jobs(scheduler, bot, pg_session_factory)

    job = scheduler.get_job(f"deliver_pending:{inst_id}:recover")
    assert job is not None
    await job.func(*job.args)

    assert bot.send_message.await_count == 1
    async with pg_session_factory() as s:
        inst = await TaskRepository(s).get_instance(inst_id)
        assert inst.delivery_status == DeliveryStatus.SENT
