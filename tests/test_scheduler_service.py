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


def test_weekly_multiple_days_picks_nearest():
    # 2026-07-10 09:00 — пятница. Дни пн/ср/пт: ближайший — понедельник 13.07.
    c = cfg(schedule_type="weekly", schedule_value="0,2,4")
    nxt = compute_next_run(c, datetime(2026, 7, 10, 9, 0))
    assert nxt == datetime(2026, 7, 13, 9, 0)
    assert nxt.weekday() == 0


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


def test_after_change_allows_same_day_if_time_not_passed():
    """rebuild_config_job (after_change=True) не должен «перепрыгивать» сегодняшний
    слот, если время ещё не наступило — иначе задача, изменённая в Sheets
    сегодня утром на сегодняшний вечер, улетит только через полный цикл."""
    c = cfg(schedule_type="every_n_days", schedule_interval=2, time=time(18, 0))
    nxt = compute_next_run(c, datetime(2026, 7, 15, 9, 0), after_change=True)
    assert nxt == datetime(2026, 7, 15, 18, 0)                # сегодня, время ещё не прошло

    c_daily = cfg(schedule_type="daily", time=time(18, 0))
    nxt_daily = compute_next_run(c_daily, datetime(2026, 7, 15, 9, 0), after_change=True)
    assert nxt_daily == datetime(2026, 7, 15, 18, 0)


def test_after_change_falls_back_when_today_time_passed():
    c = cfg(schedule_type="every_n_days", schedule_interval=2, time=time(8, 0))
    nxt = compute_next_run(c, datetime(2026, 7, 15, 9, 0), after_change=True)
    assert nxt == datetime(2026, 7, 17, 8, 0)                 # 08:00 уже прошло — обычный цикл


def test_after_change_weekly_respects_target_weekday():
    # 2026-07-15 — среда (weekday=2); понедельник (0) сегодня не подходит.
    c = cfg(schedule_type="weekly", schedule_value="0", time=time(18, 0))
    nxt = compute_next_run(c, datetime(2026, 7, 15, 9, 0), after_change=True)
    assert nxt == datetime(2026, 7, 20, 18, 0)                # ближайший понедельник, не сегодня

    c_today = cfg(schedule_type="weekly", schedule_value="2", time=time(18, 0))  # среда
    nxt_today = compute_next_run(c_today, datetime(2026, 7, 15, 9, 0), after_change=True)
    assert nxt_today == datetime(2026, 7, 15, 18, 0)          # сегодня — целевой день недели

    c_default = cfg(schedule_type="weekly", schedule_value="0", time=time(18, 0))
    nxt_default = compute_next_run(c_default, datetime(2026, 7, 15, 9, 0))  # after_change=False
    assert nxt_default == datetime(2026, 7, 20, 18, 0)        # старое поведение не сломано


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


async def test_register_delivery_log_job_enabled_interval_no_duplicates(session_factory):
    from bot.services.setting_service import SettingService

    async with session_factory() as s:
        await SettingService(s).set("delivery_log.enabled", True, actor_user_id=None)
        await SettingService(s).set("delivery_log.interval_minutes", 30,
                                    actor_user_id=None)
        await s.commit()

    scheduler = AsyncIOScheduler()
    svc = SchedulerService(scheduler, AsyncMock(), session_factory)
    for _ in range(3):                                 # идемпотентно, без дублей
        await svc.register_delivery_log_job()
    jobs = [j for j in scheduler.get_jobs() if j.id == "delivery_log"]
    assert len(jobs) == 1
    assert jobs[0].trigger.interval.total_seconds() == 30 * 60


async def test_register_delivery_log_job_disabled_removes_job(session_factory):
    from bot.services.setting_service import SettingService

    async with session_factory() as s:
        await SettingService(s).set("delivery_log.enabled", True, actor_user_id=None)
        await s.commit()
    scheduler = AsyncIOScheduler()
    svc = SchedulerService(scheduler, AsyncMock(), session_factory)
    await svc.register_delivery_log_job()
    assert scheduler.get_job("delivery_log") is not None

    async with session_factory() as s:
        await SettingService(s).set("delivery_log.enabled", False, actor_user_id=None)
        await s.commit()
    await svc.register_delivery_log_job()
    assert scheduler.get_job("delivery_log") is None    # выключили — job снят


async def test_setup_scheduler_registers_delivery_log_job(session_factory):
    from bot.services.scheduler_service import setup_scheduler
    from bot.services.setting_service import SettingService

    async with session_factory() as s:
        await SettingService(s).set("delivery_log.enabled", True, actor_user_id=None)
        await s.commit()
    scheduler = await setup_scheduler(AsyncMock(), session_factory)
    assert scheduler.get_job("delivery_log") is not None


async def test_register_status_notification_job_enabled_interval_no_duplicates(session_factory):
    from bot.services.setting_service import SettingService

    async with session_factory() as s:
        await SettingService(s).set("status_notifications.enabled", True,
                                    actor_user_id=None)
        await SettingService(s).set("status_notifications.interval_minutes", 10,
                                    actor_user_id=None)
        await s.commit()

    scheduler = AsyncIOScheduler()
    svc = SchedulerService(scheduler, AsyncMock(), session_factory)
    for _ in range(3):                                 # идемпотентно, без дублей
        await svc.register_status_notification_job()
    jobs = [j for j in scheduler.get_jobs() if j.id == "status_notifications"]
    assert len(jobs) == 1
    assert jobs[0].trigger.interval.total_seconds() == 10 * 60


async def test_register_status_notification_job_disabled_removes_job(session_factory):
    from bot.services.setting_service import SettingService

    async with session_factory() as s:
        await SettingService(s).set("status_notifications.enabled", True,
                                    actor_user_id=None)
        await s.commit()
    scheduler = AsyncIOScheduler()
    svc = SchedulerService(scheduler, AsyncMock(), session_factory)
    await svc.register_status_notification_job()
    assert scheduler.get_job("status_notifications") is not None

    async with session_factory() as s:
        await SettingService(s).set("status_notifications.enabled", False,
                                    actor_user_id=None)
        await s.commit()
    await svc.register_status_notification_job()
    assert scheduler.get_job("status_notifications") is None   # выключили — job снят


async def test_setup_scheduler_registers_status_notification_job(session_factory):
    from bot.services.scheduler_service import setup_scheduler
    from bot.services.setting_service import SettingService

    async with session_factory() as s:
        await SettingService(s).set("status_notifications.enabled", True,
                                    actor_user_id=None)
        await s.commit()
    scheduler = await setup_scheduler(AsyncMock(), session_factory)
    assert scheduler.get_job("status_notifications") is not None


async def test_register_status_history_job_enabled_interval_no_duplicates(session_factory):
    from bot.services.setting_service import SettingService

    async with session_factory() as s:
        await SettingService(s).set("status_history_log.enabled", True,
                                    actor_user_id=None)
        await SettingService(s).set("status_history_log.interval_minutes", 30,
                                    actor_user_id=None)
        await s.commit()

    scheduler = AsyncIOScheduler()
    svc = SchedulerService(scheduler, AsyncMock(), session_factory)
    for _ in range(3):                                 # идемпотентно, без дублей
        await svc.register_status_history_job()
    jobs = [j for j in scheduler.get_jobs() if j.id == "status_history"]
    assert len(jobs) == 1
    assert jobs[0].trigger.interval.total_seconds() == 30 * 60


async def test_register_status_history_job_disabled_removes_job(session_factory):
    from bot.services.setting_service import SettingService

    async with session_factory() as s:
        await SettingService(s).set("status_history_log.enabled", True,
                                    actor_user_id=None)
        await s.commit()
    scheduler = AsyncIOScheduler()
    svc = SchedulerService(scheduler, AsyncMock(), session_factory)
    await svc.register_status_history_job()
    assert scheduler.get_job("status_history") is not None

    async with session_factory() as s:
        await SettingService(s).set("status_history_log.enabled", False,
                                    actor_user_id=None)
        await s.commit()
    await svc.register_status_history_job()
    assert scheduler.get_job("status_history") is None   # выключили — job снят


async def test_setup_scheduler_registers_status_history_job(session_factory):
    from bot.services.scheduler_service import setup_scheduler
    from bot.services.setting_service import SettingService

    async with session_factory() as s:
        await SettingService(s).set("status_history_log.enabled", True,
                                    actor_user_id=None)
        await s.commit()
    scheduler = await setup_scheduler(AsyncMock(), session_factory)
    assert scheduler.get_job("status_history") is not None


async def test_register_instance_jobs_not_taken_uses_setting(session_factory):
    """not_taken:{id} планируется через reminders.not_taken_after_hours (здесь 2)."""
    from datetime import timedelta
    from bot.services.setting_service import SettingService

    async with session_factory() as s:
        user = await UserRepository(s).upsert(telegram_id=7, name="Валя",
                                              role=Role.MANAGER_WB)
        repo = TaskRepository(s)
        cfg = await repo.upsert_config(dict(
            external_task_id="not_taken_from_setting", title="Проверка",
            scenario="simple", schedule_type="daily",
            responsible_user_id=user.id, is_active=True))
        inst = await repo.create_instance_idempotent(
            cfg, datetime(2026, 7, 10, 9, 0), None,
            dict(title_snapshot="Проверка", scenario_snapshot="simple"))
        await SettingService(s).set("reminders.not_taken_after_hours", 2,
                                    actor_user_id=None)
        await s.commit()

    scheduler = AsyncIOScheduler(timezone="UTC")     # как в проде (setup_scheduler)
    svc = SchedulerService(scheduler, AsyncMock(), session_factory)
    await svc.register_instance_jobs(inst)

    job = scheduler.get_job(f"not_taken:{inst.id}")
    assert job is not None
    assert job.trigger.run_date.replace(tzinfo=None) == \
        datetime(2026, 7, 10, 9, 0) + timedelta(hours=2)     # НЕ дефолтные 12
    assert scheduler.get_job(f"remind1:{inst.id}") is None
    assert scheduler.get_job(f"remind2:{inst.id}") is None
    assert scheduler.get_job(f"overdue:{inst.id}") is None


async def test_register_instance_jobs_not_taken_default_12(session_factory):
    """Без явной настройки — дефолт 12 часов (реестр reminders.not_taken_after_hours)."""
    from datetime import timedelta

    async with session_factory() as s:
        user = await UserRepository(s).upsert(telegram_id=8, name="Валя",
                                              role=Role.MANAGER_WB)
        repo = TaskRepository(s)
        cfg = await repo.upsert_config(dict(
            external_task_id="not_taken_default", title="Проверка",
            scenario="simple", schedule_type="daily",
            responsible_user_id=user.id, is_active=True))
        inst = await repo.create_instance_idempotent(
            cfg, datetime(2026, 7, 10, 9, 0), None,
            dict(title_snapshot="Проверка", scenario_snapshot="simple"))
        await s.commit()

    scheduler = AsyncIOScheduler(timezone="UTC")
    svc = SchedulerService(scheduler, AsyncMock(), session_factory)
    await svc.register_instance_jobs(inst)
    job = scheduler.get_job(f"not_taken:{inst.id}")
    assert job.trigger.run_date.replace(tzinfo=None) == \
        datetime(2026, 7, 10, 9, 0) + timedelta(hours=12)
