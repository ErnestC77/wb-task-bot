"""Job-обработчик уведомлений owner/partner о смене статуса задач (Часть Г).

Poll-модель: каждая смена статуса уже пишется в TaskLog единственным
механизмом смены статуса — TaskRepository.transition_status; этот job
периодически выбирает ещё не разосланные записи (owner_notified_at IS NULL)
с new_status из настройки status_notifications.statuses и рассылает их ролям
из status_notifications.targets. Сами 8 мест вызова transition_status не
трогаются (осознанное решение спеки).

Запись помечается обработанной даже при сбое отправки конкретному получателю
(тот же паттерн, что эскалация в overdue_job) — иначе один заблокировавший
бота получатель зациклил бы рассылку на каждом интервале.
"""
from datetime import datetime

from bot.database.models import Role, TaskLog
from bot.database.repositories.task_repository import TaskRepository
from bot.database.repositories.user_repository import UserRepository
from bot.services.setting_service import SettingService
from bot.utils.html_utils import html_escape
from bot.utils.logger import get_logger

logger = get_logger(__name__)


async def resolve_log_actor(users: UserRepository, log: TaskLog) -> str:
    """Кто сменил статус: имя пользователя по user_id; для автоматических
    переходов (user_id IS NULL, например "auto:overdue") — action записи.
    Переиспользуется status_history_job (Часть Д, колонка «Кто»)."""
    if log.user_id is not None:
        user = await users.get_by_id(log.user_id)
        if user is not None:
            return user.name
    return log.action


async def status_notification_job(bot, session_factory) -> None:
    """(регистрируется SchedulerService.register_status_notification_job)
    Ничего не делает при status_notifications.enabled=False. Список
    уведомляемых статусов читается из status_notifications.statuses при
    КАЖДОМ прогоне (не хардкодится — спека, ред. a3e6de6)."""
    from bot.database.db import async_session_factory

    factory = session_factory or async_session_factory
    async with factory() as session:
        settings = SettingService(session)
        if not bool(await settings.get("status_notifications.enabled")):
            return
        task_repo = TaskRepository(session)
        users = UserRepository(session)
        statuses = [str(s) for s in await settings.get("status_notifications.statuses")]
        logs = await task_repo.get_unnotified_status_logs(statuses)
        if not logs:
            return
        targets = list(await settings.get("status_notifications.targets"))
        recipients = []
        for role, key in ((Role.OWNER, "owner"), (Role.PARTNER, "partner")):
            if key in targets:
                recipients.extend(await users.get_active_by_role(role))
        now = datetime.utcnow()
        for log in logs:
            inst = await task_repo.get_instance(log.task_instance_id)
            title = inst.title_snapshot if inst else f"задача #{log.task_instance_id}"
            who = await resolve_log_actor(users, log)
            text = (f"📌 {html_escape(title)}: {log.old_status or '—'} → "
                    f"{log.new_status} — {html_escape(who)}")
            for u in recipients:
                try:
                    await bot.send_message(chat_id=u.telegram_id, text=text)
                except Exception as exc:  # noqa: BLE001 — не рушим job
                    logger.warning("Status notification to %s failed: %s",
                                   u.telegram_id, exc)
            log.owner_notified_at = now
        await session.commit()
