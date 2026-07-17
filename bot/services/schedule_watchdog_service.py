"""Watchdog просроченных расписаний (Часть после инцидента 2026-07-17).

recover_jobs (scheduler_recovery_service.py) уже ловит просроченный
next_run_at, но только ОДИН РАЗ — при старте бота (misfire policy: если
job не сработал вовремя, выполняет немедленно). Пока процесс работает, если
job в живом планировщике по какой-то причине не сработал (баг расписания,
зависший event loop, исключение, проглоченное APScheduler) — next_run_at
конфига молча остаётся в прошлом, и никто не узнает, пока сотрудники не
заметят отсутствие задачи в чате (как и произошло 2026-07-17).

schedule_watchdog_job периодически проверяет то же самое условие ВО ВРЕМЯ
работы: активный TaskConfig с next_run_at в прошлом больше чем на
grace_minutes — сигнал, что что-то пошло не так, шлём алерт owner/partner.
Не трогает сам next_run_at и не создаёт TaskInstance — только уведомляет,
чтобы человек разобрался (в отличие от recover_jobs, который это активно
чинит, но только при рестарте).
"""
from datetime import datetime, timedelta

from bot.database.models import Role
from bot.database.repositories.task_repository import TaskRepository
from bot.database.repositories.user_repository import UserRepository
from bot.services.setting_service import SettingService
from bot.utils.html_utils import html_escape
from bot.utils.logger import get_logger

logger = get_logger(__name__)


async def schedule_watchdog_job(bot, session_factory) -> None:
    async with session_factory() as session:
        settings = SettingService(session)
        if not bool(await settings.get("schedule_watchdog.enabled")):
            return
        grace_minutes = int(await settings.get("schedule_watchdog.grace_minutes"))
        cutoff = datetime.utcnow() - timedelta(minutes=grace_minutes)
        repo = TaskRepository(session)
        overdue = [c for c in await repo.get_active_configs()
                  if c.next_run_at is not None and c.next_run_at < cutoff]
        if not overdue:
            return
        targets = list(await settings.get("schedule_watchdog.targets"))
        users = UserRepository(session)
        recipients = []
        for role, key in ((Role.OWNER, "owner"), (Role.PARTNER, "partner")):
            if key in targets:
                recipients.extend(await users.get_active_by_role(role))
        lines = ["⚠️ Расписание могло зависнуть — задачи не выполнились вовремя:"]
        for c in overdue:
            lines.append(f"- {html_escape(c.title)} (ожидалось {c.next_run_at.strftime('%d.%m %H:%M')})")
        text = "\n".join(lines)
        for u in recipients:
            try:
                await bot.send_message(chat_id=u.telegram_id, text=text)
            except Exception as exc:  # noqa: BLE001 — не рушим job
                logger.warning("Schedule watchdog alert to %s failed: %s", u.telegram_id, exc)
