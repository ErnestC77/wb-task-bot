from datetime import datetime, time

import pytest
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


def test_cron_valid_expression():
    c = cfg(schedule_type="cron", schedule_value="30 14 * * *")
    nxt = compute_next_run(c, datetime(2026, 7, 10, 9, 0))
    assert nxt == datetime(2026, 7, 10, 14, 30)               # тот же день, 14:30

    nxt_next_day = compute_next_run(c, datetime(2026, 7, 10, 15, 0))
    assert nxt_next_day == datetime(2026, 7, 11, 14, 30)      # время уже прошло — завтра


def test_cron_invalid_expression_raises():
    c = cfg(schedule_type="cron", schedule_value="not a cron")
    with pytest.raises(ValueError):
        compute_next_run(c, datetime(2026, 7, 10, 9, 0))


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


async def test_run_config_broken_schedule_does_not_lose_instance(session_factory):
    """compute_next_run падает (битый schedule_value у weekly) ПОСЛЕ отправки
    сообщения в Telegram — TaskInstance должен остаться закоммиченным
    (сообщение уже реально ушло, откатывать его нельзя), а config job не должен
    переустанавливаться на битом расписании (иначе он будет падать бесконечно)."""
    async with session_factory() as s:
        user = await UserRepository(s).upsert(telegram_id=3, name="Тест", role=Role.MANAGER_WB)
        c = await TaskRepository(s).upsert_config(dict(
            external_task_id="broken_weekly", title="Broken",
            scenario="article_check", schedule_type="weekly",
            schedule_value="monday",   # не int — compute_next_run упадёт на int()
            time=time(9, 0), responsible_user_id=user.id, is_active=True,
            next_run_at=datetime(2026, 7, 10, 9, 0)))
        await s.commit()
        cfg_id = c.id

    bot = AsyncMock()
    bot.send_message.return_value = AsyncMock(message_id=1, chat=AsyncMock(id=-100))
    scheduler = AsyncIOScheduler()
    svc = SchedulerService(scheduler, bot, session_factory)

    await svc.run_config(cfg_id)                          # не должно упасть наружу

    bot.send_message.assert_called_once()                 # сообщение реально отправлено
    async with session_factory() as s:
        n = await s.scalar(select(func.count(TaskInstance.id)))
        assert n == 1                                      # TaskInstance не потерян
        updated = await TaskRepository(s).get_config(cfg_id)
        assert updated.next_run_at is None                 # job не переустановлен вслепую
    assert scheduler.get_job(f"config:{cfg_id}") is None    # новый job не зарегистрирован


async def test_rebuild_config_job_no_duplicate_jobs(session_factory):
    async with session_factory() as s:
        user = await UserRepository(s).upsert(telegram_id=4, name="Тест", role=Role.MANAGER_WB)
        c = await TaskRepository(s).upsert_config(dict(
            external_task_id="rebuild_check", title="Rebuild",
            scenario="article_check", schedule_type="daily",
            time=time(9, 0), responsible_user_id=user.id, is_active=True))
        await s.commit()
        cfg_id = c.id

    scheduler = AsyncIOScheduler()
    svc = SchedulerService(scheduler, AsyncMock(), session_factory)

    for _ in range(3):                                     # 3 вызова подряд — как при
        await svc.rebuild_config_job(cfg_id)                # многократном сохранении настроек

    jobs = [j for j in scheduler.get_jobs() if j.id == f"config:{cfg_id}"]
    assert len(jobs) == 1                                  # без дублей, а не растёт с каждым вызовом


async def test_setup_scheduler_uses_utc_timezone(session_factory):
    """Regression (Task 38, найдено интеграционным тестом на реальном
    ожидании): AsyncIOScheduler() без явного timezone берёт локальный часовой
    пояс ОС и интерпретирует наивные (UTC) run_date неверно — на хосте с
    локальным поясом != UTC job'ы либо срабатывают не в то время, либо молча
    пропускаются как "безнадёжно просроченные" (misfire_grace_time). Этот
    тест проверяет ИМЕННО продовую функцию setup_scheduler (не отдельно
    сконструированный в тесте scheduler) — предыдущая версия покрытия
    страховала только тестовые schedulers и не поймала бы регресс в самой
    setup_scheduler, если бы кто-то убрал timezone="UTC" оттуда."""
    from bot.services.scheduler_service import setup_scheduler

    scheduler = await setup_scheduler(AsyncMock(), session_factory)
    assert str(scheduler.timezone) == "UTC"
