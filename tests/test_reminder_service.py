from datetime import datetime
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from bot.database.models import Role, TaskStatus
from bot.database.repositories.task_repository import TaskRepository
from bot.database.repositories.user_repository import UserRepository
from bot.services import reminder_service
from bot.services.reminder_service import overdue_job, reminder_job
from bot.utils.datetime_utils import shift_to_morning
from bot.utils.validation import validate_time_str


async def seed_open_instance(session_factory, status=TaskStatus.CREATED):
    async with session_factory() as s:
        user = await UserRepository(s).upsert(telegram_id=10, name="Валя",
                                              role=Role.MANAGER_WB)
        repo = TaskRepository(s)
        cfg = await repo.upsert_config(dict(
            external_task_id="t", title="Проверка", scenario="article_check",
            schedule_type="every_n_days", schedule_interval=2,
            responsible_user_id=user.id, is_active=True))
        inst = await repo.create_instance_idempotent(
            cfg, datetime(2026, 7, 10, 9), datetime(2026, 7, 10, 12),
            dict(title_snapshot="Проверка", scenario_snapshot="article_check",
                 remind_after_hours_snapshot=3, topic_snapshot=42))
        if status != TaskStatus.CREATED:
            await repo.transition_status(inst.id, [TaskStatus.CREATED], status, None, "x")
        await s.commit()
        return inst.id


def _pin_now(monkeypatch, dt: datetime) -> None:
    """Тесты не должны зависеть от реального времени суток (тихие часы по
    умолчанию — 22:00-08:00, т.е. ~40% суток): подменяем now_tz внутри
    reminder_service, чтобы поведение было детерминированным."""
    monkeypatch.setattr(reminder_service, "now_tz",
                        lambda tz_name: dt.replace(tzinfo=ZoneInfo(tz_name)))


async def test_reminder_sent_to_topic(session_factory, monkeypatch):
    _pin_now(monkeypatch, datetime(2026, 7, 10, 12, 0))     # заведомо не тихие часы
    inst_id = await seed_open_instance(session_factory)
    bot = AsyncMock()
    await reminder_job(inst_id, 1, bot, session_factory, AsyncIOScheduler())
    bot.send_message.assert_awaited_once()
    kwargs = bot.send_message.await_args.kwargs
    assert kwargs["message_thread_id"] == 42
    assert "Напоминание" in kwargs["text"] and "Проверка" in kwargs["text"]


async def test_reminder_skipped_for_closed_task(session_factory, monkeypatch):
    _pin_now(monkeypatch, datetime(2026, 7, 10, 12, 0))
    inst_id = await seed_open_instance(session_factory, TaskStatus.CANCELLED)
    bot = AsyncMock()
    await reminder_job(inst_id, 1, bot, session_factory, AsyncIOScheduler())
    bot.send_message.assert_not_awaited()


async def test_reminder_shifted_during_quiet_hours_not_sent_immediately(
        session_factory, monkeypatch):
    """22:00-08:00 (default) — ночью напоминание не должно уходить сразу;
    reminders_sent не должен расти (иначе перенесённое напоминание "сгорит"
    как отправленное, хотя фактически его никто не получил)."""
    _pin_now(monkeypatch, datetime(2026, 7, 10, 23, 30))     # внутри тихих часов
    inst_id = await seed_open_instance(session_factory)
    bot = AsyncMock()
    scheduler = AsyncIOScheduler()
    await reminder_job(inst_id, 1, bot, session_factory, scheduler)
    bot.send_message.assert_not_awaited()
    async with session_factory() as s:
        inst = await TaskRepository(s).get_instance(inst_id)
        assert inst.reminders_sent == 0


async def test_reminder_hitting_quiet_hours_is_actually_rescheduled(
        session_factory, monkeypatch):
    """Регрессия на находку ревьюера (Critical): раньше reminder_job не получал
    ссылку на scheduler и при попадании в тихие часы просто логировал и делал
    return — job (trigger="date") одноразовый, поэтому напоминание пропадало
    из job store навсегда без реального переноса. Теперь reminder_job должен
    сам себя переставить на конец тихих часов через scheduler.add_job(...), и
    при срабатывании нового job'а сообщение должно реально уйти."""
    now = datetime(2026, 7, 10, 23, 30)                      # внутри тихих часов
    _pin_now(monkeypatch, now)
    inst_id = await seed_open_instance(session_factory)
    bot = AsyncMock()
    scheduler = AsyncIOScheduler()

    await reminder_job(inst_id, 1, bot, session_factory, scheduler)

    bot.send_message.assert_not_awaited()                    # ночью не ушло сразу

    end = validate_time_str("08:00")                         # default quiet_hours_end
    # reminder_job сравнивает/переносит время в general.timezone (default
    # Europe/Moscow, см. _pin_now выше) — считаем ожидаемое значение так же,
    # иначе сравнение с job.trigger.run_date (он tz-aware) даст ложный fail.
    now_aware = now.replace(tzinfo=ZoneInfo("Europe/Moscow"))
    expected_run_date = shift_to_morning(now_aware, end)
    job = scheduler.get_job(f"remind1:{inst_id}:shifted")
    assert job is not None                                    # job реально переставлен
    assert job.trigger.run_date == expected_run_date

    # реально исполняем перепланированный job — "утром", вне тихих часов
    _pin_now(monkeypatch, expected_run_date.replace(tzinfo=None))
    await job.func(*job.args)

    bot.send_message.assert_awaited_once()                    # сообщение реально дошло
    async with session_factory() as s:
        inst = await TaskRepository(s).get_instance(inst_id)
        assert inst.reminders_sent == 1


async def test_overdue_only_from_open_statuses(session_factory):
    inst_id = await seed_open_instance(session_factory)
    bot = AsyncMock()
    await overdue_job(inst_id, bot, session_factory)
    async with session_factory() as s:
        inst = await TaskRepository(s).get_instance(inst_id)
        assert inst.status == TaskStatus.OVERDUE
    # повторный вызов — статус уже overdue, переход не выполняется повторно
    await overdue_job(inst_id, bot, session_factory)
    async with session_factory() as s:
        from sqlalchemy import func, select
        from bot.database.models import TaskLog
        n = await s.scalar(select(func.count(TaskLog.id))
                           .where(TaskLog.new_status == TaskStatus.OVERDUE))
        assert n == 1


async def test_overdue_escalation_sent_to_owner(session_factory, monkeypatch):
    inst_id = await seed_open_instance(session_factory)
    async with session_factory() as s:
        await UserRepository(s).upsert(telegram_id=99, name="Хозяин", role=Role.OWNER)
        from bot.services.setting_service import SettingService
        settings = SettingService(s)
        await settings.set("reminders.escalation_enabled", True, actor_user_id=None)
        await s.commit()
    bot = AsyncMock()
    await overdue_job(inst_id, bot, session_factory)
    bot.send_message.assert_awaited_once()
    kwargs = bot.send_message.await_args.kwargs
    assert kwargs["chat_id"] == 99
    assert "Проверка" in kwargs["text"]
