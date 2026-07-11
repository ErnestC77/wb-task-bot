"""Task 38 Step 6: DeliveryService с реальным AsyncIOScheduler и фейковым Bot,
падающим 2 раза подряд, — проверяет реальное поведение retry на PostgreSQL
(delivery_attempts персистится в БД между попытками, retry-job реально
регистрируется в планировщике). Джобы выполняются НАПРЯМУЮ (`await
job.func(*job.args)`) вместо реального ожидания `next_retry_at` — брифовая
цель "не спать реальные секунды" достигается так же надёжно, но без
зависимости от значения `general.telegram_retry_intervals`."""
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from bot.database.models import DeliveryStatus, Role
from bot.database.repositories.task_repository import TaskRepository
from bot.database.repositories.user_repository import UserRepository
from bot.services.delivery_service import DeliveryService
from bot.services.task_service import TaskService

pytestmark = pytest.mark.pg


async def test_delivery_retries_twice_then_succeeds_on_postgres(pg_session_factory):
    async with pg_session_factory() as s:
        user = await UserRepository(s).upsert(telegram_id=1, name="V", role=Role.MANAGER_WB)
        cfg = await TaskRepository(s).upsert_config(dict(
            external_task_id="retry_pg", title="t", schedule_type="daily",
            responsible_user_id=user.id, is_active=True))
        inst = await TaskService(s).create_instance_for(cfg, datetime.utcnow())
        await s.commit()
        inst_id = inst.id

    scheduler = AsyncIOScheduler()
    bot = AsyncMock()
    bot.send_message.side_effect = [
        Exception("сеть недоступна, попытка 1"),
        Exception("сеть недоступна, попытка 2"),
        SimpleNamespace(message_id=1, chat=SimpleNamespace(id=-1)),
    ]

    async with pg_session_factory() as s:
        inst = await TaskRepository(s).get_instance(inst_id)
        delivery = DeliveryService(s, bot, scheduler)
        ok = await delivery.send_task_message(inst)          # попытка 1 — падает
        await s.commit()
        assert ok is False
        assert inst.delivery_status == DeliveryStatus.RETRYING
        assert inst.delivery_attempts == 1

    job = scheduler.get_job(f"retry_delivery:{inst_id}:1")
    assert job is not None
    await job.func(*job.args)                                # попытка 2 — падает

    async with pg_session_factory() as s:
        inst = await TaskRepository(s).get_instance(inst_id)
        assert inst.delivery_status == DeliveryStatus.RETRYING
        assert inst.delivery_attempts == 2

    job = scheduler.get_job(f"retry_delivery:{inst_id}:2")
    assert job is not None
    await job.func(*job.args)                                # попытка 3 — успех

    async with pg_session_factory() as s:
        inst = await TaskRepository(s).get_instance(inst_id)
        assert inst.delivery_status == DeliveryStatus.SENT
        assert inst.delivery_attempts == 3

    assert bot.send_message.await_count == 3
