from datetime import datetime, time

from unittest.mock import AsyncMock

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import func, select

from bot.database.models import Role, TaskConfig, TaskInstance
from bot.database.repositories.task_repository import TaskRepository
from bot.database.repositories.user_repository import UserRepository
from bot.services.scheduler_service import SchedulerService, compute_next_run


def cfg(**kw):
    base = dict(external_task_id="x", title="x", schedule_type="daily",
                time=time(9, 0), run_on_weekends=True)
    base.update(kw)
    return TaskConfig(**base)


def test_every_n_days_interval_2():
    c = cfg(schedule_type="every_n_days", schedule_interval=2)
    nxt = compute_next_run(c, after=datetime(2026, 7, 10, 9, 0))
    assert nxt == datetime(2026, 7, 12, 9, 0)          # раз в 2 дня


def test_daily_and_weekly():
    assert compute_next_run(cfg(), datetime(2026, 7, 10, 10, 0)) == datetime(2026, 7, 11, 9, 0)
    c = cfg(schedule_type="weekly", schedule_value="0")  # понедельник
    assert compute_next_run(c, datetime(2026, 7, 10, 9, 0)) == datetime(2026, 7, 13, 9, 0)


def test_monthly_clamps_to_month_end():
    c = cfg(schedule_type="monthly", schedule_value="31")
    nxt = compute_next_run(c, datetime(2026, 2, 1, 0, 0))
    assert (nxt.month, nxt.day) == (2, 28)


def test_skip_weekends():
    c = cfg(schedule_type="daily", run_on_weekends=False)
    nxt = compute_next_run(c, datetime(2026, 7, 10, 10, 0))  # пятница
    assert nxt.weekday() == 0                                # понедельник 13.07


async def test_run_config_idempotent(session_factory):
    async with session_factory() as s:
        user = await UserRepository(s).upsert(telegram_id=1, name="Валя", role=Role.MANAGER_WB)
        cfg = await TaskRepository(s).upsert_config(dict(
            external_task_id="articles_check_all", title="Проверка",
            scenario="article_check", schedule_type="every_n_days", schedule_interval=2,
            time=time(9, 0), responsible_user_id=user.id, is_active=True,
            next_run_at=datetime(2026, 7, 10, 9, 0)))
        await s.commit()
        cfg_id = cfg.id

    bot = AsyncMock()
    bot.send_message.return_value = AsyncMock(message_id=1, chat=AsyncMock(id=-100))
    svc = SchedulerService(AsyncIOScheduler(), bot, session_factory)
    await svc.run_config(cfg_id)
    # эмулируем повторный запуск того же планового времени (misfire/дубль job)
    async with session_factory() as s:
        c = await TaskRepository(s).get_config(cfg_id)
        c.next_run_at = datetime(2026, 7, 10, 9, 0)
        await s.commit()
    await svc.run_config(cfg_id)
    async with session_factory() as s:
        n = await s.scalar(select(func.count(TaskInstance.id)))
        assert n == 1                                     # дубля нет
