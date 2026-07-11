"""Job-обработчики напоминаний и просрочки задач (Task 14).

Оба job-а открывают собственную сессию через ``session_factory``, сами
коммитят изменения и глотают/логируют ошибки отправки в Telegram —
недоставленное напоминание/эскалация не должны ронять APScheduler job.
"""
from bot.database.models import Role, TaskStatus
from bot.database.repositories.task_repository import OPEN_STATUSES, TaskRepository
from bot.database.repositories.user_repository import UserRepository
from bot.services.setting_service import SettingService
from bot.utils.datetime_utils import is_quiet_hours, now_tz, shift_to_morning
from bot.utils.html_utils import html_escape
from bot.utils.logger import get_logger
from bot.utils.message_templates import render_reminder
from bot.utils.validation import validate_time_str

logger = get_logger(__name__)


async def reminder_job(instance_id: int, number: int, bot, session_factory) -> None:
    async with session_factory() as session:
        repo = TaskRepository(session)
        settings = SettingService(session)
        inst = await repo.get_instance(instance_id)
        if inst is None or inst.status not in OPEN_STATUSES:
            return
        if inst.reminders_sent >= int(await settings.get("reminders.max_count")):
            return

        # reminders.quiet_hours_* задаются админом как локальное настенное
        # время (general.timezone), поэтому "сейчас" тоже берём в этой зоне —
        # сравнение с сырым UTC давало бы неверный результат для любого
        # часового пояса, отличного от UTC+0.
        tz_name = str(await settings.get("general.timezone"))
        now = now_tz(tz_name)
        start = validate_time_str(str(await settings.get("reminders.quiet_hours_start")))
        end = validate_time_str(str(await settings.get("reminders.quiet_hours_end")))
        if is_quiet_hours(now, start, end):
            if bool(await settings.get("reminders.shift_night_to_morning")):
                # Настоящую пересборку job'а в APScheduler здесь не сделать:
                # reminder_job получает только (instance_id, number, bot,
                # session_factory) — без ссылки на scheduler (сигнатура
                # зафиксирована в Task 12, SchedulerService.register_instance_jobs,
                # и переиспользуется в scheduler_recovery_service — Task 13).
                # Поэтому ночью просто не отправляем и фиксируем в логе, во
                # сколько напоминание было бы уместно; reminders_sent не
                # увеличиваем, чтобы напоминание не считалось "потраченным".
                logger.info("Reminder %s for instance=%s shifted to %s (quiet hours)",
                            number, instance_id, shift_to_morning(now, end))
            else:
                logger.info("Reminder %s for instance=%s dropped (quiet hours, "
                            "shift_night_to_morning disabled)", number, instance_id)
            return

        text = render_reminder(inst, str(await settings.get("reminders.text_template")))
        targets = list(await settings.get("reminders.targets"))
        chat_id = int(await settings.get("general.group_chat_id"))
        users = UserRepository(session)
        try:
            if "topic" in targets:
                await bot.send_message(chat_id=chat_id,
                                       message_thread_id=inst.topic_snapshot, text=text)
            if "responsible_private" in targets and inst.responsible_user:
                await bot.send_message(chat_id=inst.responsible_user.telegram_id, text=text)
            for role, key in ((Role.OWNER, "owner"), (Role.PARTNER, "partner")):
                if key in targets:
                    for u in await users.get_active_by_role(role):
                        await bot.send_message(chat_id=u.telegram_id, text=text)
        except Exception as exc:                     # noqa: BLE001 — не рушим job
            logger.warning("Reminder send failed for instance=%s: %s", instance_id, exc)
        inst.reminders_sent += 1
        await session.commit()


async def overdue_job(instance_id: int, bot, session_factory) -> None:
    async with session_factory() as session:
        repo = TaskRepository(session)
        settings = SettingService(session)
        if not bool(await settings.get("reminders.overdue_enabled")):
            return
        got = await repo.transition_status(
            instance_id, list(OPEN_STATUSES), TaskStatus.OVERDUE, None, "auto:overdue")
        if got is None:
            # Инстанса нет либо статус уже не created/in_progress/postponed
            # (задача выполнена/отменена/уже overdue) — переход не выполняем.
            return
        if bool(await settings.get("reminders.escalation_enabled")):
            users = UserRepository(session)
            targets = list(await settings.get("reminders.escalation_targets"))
            text = f"🔥 Просрочена задача: {html_escape(got.title_snapshot)}"
            for role, key in ((Role.OWNER, "owner"), (Role.PARTNER, "partner")):
                if key in targets:
                    for u in await users.get_active_by_role(role):
                        try:
                            await bot.send_message(chat_id=u.telegram_id, text=text)
                        except Exception as exc:     # noqa: BLE001 — не рушим job
                            logger.warning("Escalation to %s failed: %s", u.telegram_id, exc)
        await session.commit()
