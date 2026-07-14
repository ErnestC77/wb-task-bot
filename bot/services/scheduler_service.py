import calendar
from datetime import datetime, time, timedelta

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from bot.database.models import ScheduleType, TaskConfig
from bot.utils.logger import get_logger

logger = get_logger(__name__)
GRACE = 3600


def _at(day, t: time | None) -> datetime:
    return datetime.combine(day, t or time(9, 0))


def _skip_weekend(dt: datetime, allow: bool) -> datetime:
    while not allow and dt.weekday() >= 5:
        dt += timedelta(days=1)
    return dt


def compute_next_run(config: TaskConfig, after: datetime) -> datetime | None:
    st = config.schedule_type
    if st == ScheduleType.DAILY:
        nxt = _at(after.date() + timedelta(days=1), config.time)
    elif st == ScheduleType.EVERY_N_DAYS:
        nxt = _at(after.date() + timedelta(days=config.schedule_interval or 1), config.time)
    elif st == ScheduleType.WEEKLY:
        # schedule_value: одно число (0=понедельник) или несколько через
        # запятую ("0,2,4" — пн/ср/пт) — берём ближайший из перечисленных дней.
        targets = [int(v) for v in str(config.schedule_value or "0").split(",") if v.strip()]
        days = min((t - after.weekday() - 1) % 7 + 1 for t in targets)
        nxt = _at(after.date() + timedelta(days=days), config.time)
    elif st == ScheduleType.MONTHLY:
        target_day = int(config.schedule_value or 1)
        year, month = after.year, after.month
        candidate = _at(after.date().replace(
            day=min(target_day, calendar.monthrange(year, month)[1])), config.time)
        if candidate <= after:
            month = 1 if month == 12 else month + 1
            year = year + 1 if month == 1 else year
            candidate = _at(datetime(year, month, 1).date().replace(
                day=min(target_day, calendar.monthrange(year, month)[1])), config.time)
        nxt = candidate
    elif st == ScheduleType.CRON:
        trigger = CronTrigger.from_crontab(config.schedule_value or "0 9 * * *")
        fire = trigger.get_next_fire_time(None, after)
        nxt = fire.replace(tzinfo=None) if fire else None
    else:
        return None
    return _skip_weekend(nxt, config.run_on_weekends) if nxt else None


class SchedulerService:
    def __init__(self, scheduler: AsyncIOScheduler, bot: Bot, session_factory) -> None:
        self.scheduler = scheduler
        self.bot = bot
        self.session_factory = session_factory

    async def run_config(self, config_id: int) -> None:
        from bot.database.repositories.task_repository import TaskRepository
        from bot.services.delivery_service import DeliveryService
        from bot.services.task_service import TaskService

        async with self.session_factory() as session:
            repo = TaskRepository(session)
            config = await repo.get_config(config_id)
            if config is None or not config.is_active:
                return
            scheduled_at = config.next_run_at or datetime.utcnow().replace(
                second=0, microsecond=0)
            inst = await TaskService(session, self.bot).create_instance_for(
                config, scheduled_at)
            if inst is not None:
                await DeliveryService(session, self.bot, self.scheduler
                                      ).send_task_message(inst)
                self.register_instance_jobs(inst)
            try:
                config.next_run_at = compute_next_run(config, scheduled_at)
            except Exception:
                # Сообщение в Telegram (если inst создан) уже реально отправлено —
                # это необратимо. TaskInstance (если создан) остаётся в БД: коммитим
                # то, что уже сделано, а next_run_at сбрасываем в None, чтобы
                # register_config_job ниже НЕ пересоздал job на битом расписании
                # (иначе он будет падать на каждом срабатывании бесконечно).
                # Job для конфига больше не переустанавливается — пока админ не
                # поправит schedule_value/schedule_type и не вызовет rebuild_config_job.
                logger.exception(
                    "compute_next_run failed for config_id=%s schedule_type=%s "
                    "schedule_value=%s: schedule is broken, config job will not "
                    "be rescheduled until fixed",
                    config.id, config.schedule_type, config.schedule_value,
                )
                config.next_run_at = None
                await session.commit()
                return
            await session.commit()
            self.register_config_job(config)

    def register_config_job(self, config: TaskConfig) -> None:
        if config.next_run_at is None or not config.is_active:
            return
        self.scheduler.add_job(
            self.run_config, "date", run_date=config.next_run_at, args=[config.id],
            id=f"config:{config.id}", replace_existing=True, misfire_grace_time=GRACE)

    async def rebuild_config_job(self, config_id: int) -> None:
        """Вызывается админ-панелью после изменения расписания."""
        from bot.database.repositories.task_repository import TaskRepository
        async with self.session_factory() as session:
            config = await TaskRepository(session).get_config(config_id)
            if config is None:
                return
            base = datetime.utcnow()
            config.next_run_at = (compute_next_run(config, base)
                                  if config.is_active else None)
            await session.commit()
            job_id = f"config:{config_id}"
            if self.scheduler.get_job(job_id):
                self.scheduler.remove_job(job_id)
            self.register_config_job(config)

    def register_instance_jobs(self, inst) -> None:
        from bot.services.reminder_service import reminder_job, overdue_job
        base = inst.scheduled_at
        if inst.remind_after_hours_snapshot:
            self.scheduler.add_job(
                reminder_job, "date",
                run_date=base + timedelta(hours=inst.remind_after_hours_snapshot),
                args=[inst.id, 1, self.bot, self.session_factory, self.scheduler],
                id=f"remind1:{inst.id}", replace_existing=True, misfire_grace_time=GRACE)
        if inst.second_remind_after_hours_snapshot:
            self.scheduler.add_job(
                reminder_job, "date",
                run_date=base + timedelta(hours=inst.second_remind_after_hours_snapshot),
                args=[inst.id, 2, self.bot, self.session_factory, self.scheduler],
                id=f"remind2:{inst.id}", replace_existing=True, misfire_grace_time=GRACE)
        self.scheduler.add_job(
            overdue_job, "date", run_date=base + timedelta(hours=24),
            args=[inst.id, self.bot, self.session_factory],
            id=f"overdue:{inst.id}", replace_existing=True, misfire_grace_time=GRACE)

    async def register_report_job(self) -> None:
        from bot.services.report_service import weekly_report_job
        from bot.services.setting_service import SettingService
        async with self.session_factory() as session:
            settings = SettingService(session)
            weekday = int(await settings.get("reports.weekday"))
            hh, mm = str(await settings.get("reports.time")).split(":")
        # Явный remove_job перед add_job: replace_existing=True сам по себе
        # идемпотентен только для уже стартовавшего планировщика (находка
        # Task 12-13, см. тот же паттерн в register_sync_job/rebuild_config_job
        # ниже) — до scheduler.start() второй add_job с тем же id копится в
        # _pending_jobs рядом с первым вместо замены, из-за чего
        # get_job("weekly_report") продолжает возвращать job со СТАРЫМ
        # временем после повторного вызова (найдено регрессионным тестом
        # Task 36 на изменение reports.time через админ-панель).
        if self.scheduler.get_job("weekly_report"):
            self.scheduler.remove_job("weekly_report")
        self.scheduler.add_job(
            weekly_report_job, "cron", day_of_week=weekday, hour=int(hh), minute=int(mm),
            args=[self.bot, self.session_factory], id="weekly_report",
            replace_existing=True, misfire_grace_time=GRACE)

    async def register_sync_job(self) -> None:
        from bot.services.google_sheets_service import auto_sync_job
        from bot.services.setting_service import SettingService
        async with self.session_factory() as session:
            settings = SettingService(session)
            enabled = bool(await settings.get("sync.auto_enabled"))
            minutes = int(await settings.get("sync.interval_minutes"))
        if self.scheduler.get_job("auto_sync"):
            self.scheduler.remove_job("auto_sync")
        if enabled:
            # self третьим аргументом: auto_sync_job после коммита вызывает
            # rebuild_config_job для изменённых синком конфигов (Часть А).
            self.scheduler.add_job(auto_sync_job, "interval", minutes=minutes,
                                   args=[self.bot, self.session_factory, self],
                                   id="auto_sync", misfire_grace_time=GRACE)

    async def register_delivery_log_job(self) -> None:
        """Часть Б: по образцу register_sync_job — снимает job "delivery_log"
        и, если delivery_log.enabled=True, регистрирует delivery_log_job с
        интервалом delivery_log.interval_minutes. Явный remove_job перед
        add_job — см. комментарий в register_report_job (до scheduler.start()
        replace_existing не заменяет, а копит job'ы в _pending_jobs)."""
        from bot.services.google_sheets_service import delivery_log_job
        from bot.services.setting_service import SettingService
        async with self.session_factory() as session:
            settings = SettingService(session)
            enabled = bool(await settings.get("delivery_log.enabled"))
            minutes = int(await settings.get("delivery_log.interval_minutes"))
        if self.scheduler.get_job("delivery_log"):
            self.scheduler.remove_job("delivery_log")
        if enabled:
            self.scheduler.add_job(delivery_log_job, "interval", minutes=minutes,
                                   args=[self.bot, self.session_factory],
                                   id="delivery_log", misfire_grace_time=GRACE)

    async def register_status_notification_job(self) -> None:
        """Часть Г: по образцу register_sync_job/register_delivery_log_job —
        снимает job "status_notifications" и, если
        status_notifications.enabled=True, регистрирует status_notification_job
        с интервалом status_notifications.interval_minutes. Явный remove_job
        перед add_job — см. комментарий в register_report_job (до
        scheduler.start() replace_existing не заменяет, а копит job'ы)."""
        from bot.services.setting_service import SettingService
        from bot.services.status_notification_service import status_notification_job
        async with self.session_factory() as session:
            settings = SettingService(session)
            enabled = bool(await settings.get("status_notifications.enabled"))
            minutes = int(await settings.get("status_notifications.interval_minutes"))
        if self.scheduler.get_job("status_notifications"):
            self.scheduler.remove_job("status_notifications")
        if enabled:
            self.scheduler.add_job(status_notification_job, "interval", minutes=minutes,
                                   args=[self.bot, self.session_factory],
                                   id="status_notifications", misfire_grace_time=GRACE)

    async def register_status_history_job(self) -> None:
        """Часть Д: по образцу register_delivery_log_job — снимает job
        "status_history" и, если status_history_log.enabled=True, регистрирует
        status_history_job с интервалом status_history_log.interval_minutes.
        Явный remove_job перед add_job — см. комментарий в register_report_job."""
        from bot.services.google_sheets_service import status_history_job
        from bot.services.setting_service import SettingService
        async with self.session_factory() as session:
            settings = SettingService(session)
            enabled = bool(await settings.get("status_history_log.enabled"))
            minutes = int(await settings.get("status_history_log.interval_minutes"))
        if self.scheduler.get_job("status_history"):
            self.scheduler.remove_job("status_history")
        if enabled:
            self.scheduler.add_job(status_history_job, "interval", minutes=minutes,
                                   args=[self.bot, self.session_factory],
                                   id="status_history", misfire_grace_time=GRACE)


async def setup_scheduler(bot: Bot, session_factory) -> AsyncIOScheduler:
    from bot.database.repositories.task_repository import TaskRepository

    # timezone="UTC" ОБЯЗАТЕЛЕН (найдено интеграционным тестом на реальном
    # времени, Task 38): весь проект планирует job'ы наивным `datetime.utcnow()`
    # (compute_next_run, _not_past, reminder/overdue/auto_approve дедлайны и
    # т.д.), а APScheduler без явного timezone берёт локальный часовой пояс
    # ОС (`tzlocal`) и интерпретирует ЛЮБОЙ наивный `run_date` как локальное
    # время. На сервере с локальным поясом ≠ UTC это сдвигает КАЖДЫЙ job на
    # величину смещения: при сдвиге больше misfire_grace_time (`GRACE = 3600`
    # везде в этом файле) job молча НЕ выполняется вообще (APScheduler
    # считает его безнадёжно пропущенным). Базовый образ Dockerfile обычно
    # UTC по умолчанию, поэтому баг латентный — но зависит от ОС/деплоя, а не
    # от кода, что и обнаружил тест с реальным ожиданием на машине с
    # локальным поясом UTC+4.
    scheduler = AsyncIOScheduler(timezone="UTC")
    svc = SchedulerService(scheduler, bot, session_factory)
    scheduler.wb_service = svc                     # доступ из handlers через dispatcher
    async with session_factory() as session:
        repo = TaskRepository(session)
        for config in await repo.get_active_configs():
            if config.next_run_at is None:
                config.next_run_at = compute_next_run(config, datetime.utcnow())
        await session.commit()
        for config in await repo.get_active_configs():
            svc.register_config_job(config)
    await svc.register_report_job()
    await svc.register_sync_job()
    await svc.register_delivery_log_job()
    await svc.register_status_notification_job()
    await svc.register_status_history_job()
    return scheduler
