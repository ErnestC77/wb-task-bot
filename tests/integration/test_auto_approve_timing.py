from datetime import datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from bot.database.models import Role, TaskStatus
from bot.database.repositories.task_repository import TaskRepository
from bot.database.repositories.user_repository import UserRepository
from bot.services.approval_service import auto_approve_job
from bot.services.task_service import TaskService

pytestmark = pytest.mark.pg


async def test_auto_approve_fires_after_short_timeout(pg_session_factory):
    """Использует ТЕСТОВЫЙ дедлайн в секундах (не реальные часы прод-настройки),
    чтобы не спать 24 часа в CI."""
    async with pg_session_factory() as s:
        user = await UserRepository(s).upsert(telegram_id=1, name="V",
                                              role=Role.MANAGER_WB)
        cfg = await TaskRepository(s).upsert_config(dict(
            external_task_id="t", title="t", schedule_type="daily",
            responsible_user_id=user.id, need_approval=True, is_active=True))
        inst = await TaskService(s).create_instance_for(cfg, datetime.utcnow())
        await TaskRepository(s).transition_status(
            inst.id, [TaskStatus.CREATED], TaskStatus.IN_PROGRESS, user.id, "x")
        await s.commit()
        inst_id = inst.id

    # timezone="UTC" — иначе на машине с локальным поясом != UTC APScheduler
    # интерпретирует наивный run_date (посчитанный от datetime.utcnow()) как
    # локальное время и не срабатывает вовремя (см. фикс в scheduler_service.py,
    # найденный именно этим тестом).
    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.start()
    deadline = datetime.utcnow() + timedelta(seconds=1)   # тестовый "почти сразу"
    scheduler.add_job(auto_approve_job, "date", run_date=deadline,
                      args=[inst_id, AsyncMock(), pg_session_factory],
                      id=f"auto_approve:{inst_id}")

    async with pg_session_factory() as s:
        await TaskRepository(s).transition_status(
            inst_id, [TaskStatus.IN_PROGRESS], TaskStatus.WAITING_APPROVAL, None, "x",
            approval_deadline_at=deadline)
        await s.commit()

    import asyncio
    await asyncio.sleep(2)                                 # реальное ожидание — 2с, не 24ч
    scheduler.shutdown(wait=False)

    async with pg_session_factory() as s:
        inst = await TaskRepository(s).get_instance(inst_id)
        assert inst.status == TaskStatus.AUTO_APPROVED
