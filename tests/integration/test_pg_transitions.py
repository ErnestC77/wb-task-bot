import asyncio
from datetime import datetime

import pytest

from bot.database.models import Role, TaskStatus
from bot.database.repositories.task_repository import TaskRepository
from bot.database.repositories.user_repository import UserRepository
from bot.services.task_service import TaskService

pytestmark = pytest.mark.pg


async def test_concurrent_transitions_only_one_wins(pg_session_factory):
    """Два 'параллельных' callback-а (двойное нажатие) — только один должен
    успешно перевести статус; тест реального advisory-подобного поведения
    SELECT FOR UPDATE в PostgreSQL (в SQLite это невозможно проверить)."""
    async with pg_session_factory() as s:
        user = await UserRepository(s).upsert(telegram_id=1, name="V",
                                              role=Role.MANAGER_WB)
        cfg = await TaskRepository(s).upsert_config(dict(
            external_task_id="race", title="t", schedule_type="daily",
            responsible_user_id=user.id, is_active=True))
        inst = await TaskService(s).create_instance_for(cfg, datetime(2026, 7, 10, 9))
        await s.commit()
        inst_id, user_id = inst.id, user.id

    async def attempt():
        async with pg_session_factory() as s:
            user = await UserRepository(s).get_by_id(user_id)
            got = await TaskService(s).user_transition(
                inst_id, [TaskStatus.CREATED], TaskStatus.IN_PROGRESS, user, "btn:start")
            await s.commit()
            return got is not None

    results = await asyncio.gather(attempt(), attempt())
    assert sum(results) == 1                             # ровно один переход прошёл


async def test_concurrent_transitions_produce_exactly_one_task_log(pg_session_factory):
    """Дополнительная проверка (не в брифе дословно, но напрашивается из
    финального чек-листа: 'двойное нажатие... не создаёт вторую запись в
    task_logs') — гонка не должна оставить дублирующийся TaskLog."""
    from sqlalchemy import select

    from bot.database.models import TaskLog

    async with pg_session_factory() as s:
        user = await UserRepository(s).upsert(telegram_id=2, name="V2",
                                              role=Role.MANAGER_WB)
        cfg = await TaskRepository(s).upsert_config(dict(
            external_task_id="race2", title="t", schedule_type="daily",
            responsible_user_id=user.id, is_active=True))
        inst = await TaskService(s).create_instance_for(cfg, datetime(2026, 7, 10, 9))
        await s.commit()
        inst_id, user_id = inst.id, user.id

    async def attempt():
        async with pg_session_factory() as s:
            user = await UserRepository(s).get_by_id(user_id)
            got = await TaskService(s).user_transition(
                inst_id, [TaskStatus.CREATED], TaskStatus.IN_PROGRESS, user, "btn:start")
            await s.commit()
            return got is not None

    await asyncio.gather(attempt(), attempt())

    async with pg_session_factory() as s:
        logs = list(await s.scalars(
            select(TaskLog).where(TaskLog.task_instance_id == inst_id)))
        assert len(logs) == 1
