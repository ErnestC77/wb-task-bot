"""Job-обработчик единственного напоминания «задача не взята в работу»
(заменяет прежние reminder_job/remind1/remind2 и overdue_job/эскалацию).

Одноразовый job (APScheduler trigger="date") сам по себе гарантирует, что
сообщение уйдёт максимум один раз на инстанс — никакого счётчика отправленных
напоминаний не требуется.
"""
from bot.database.models import TaskStatus
from bot.database.repositories.task_repository import TaskRepository
from bot.services.setting_service import SettingService
from bot.utils.html_utils import bold, html_escape, mention
from bot.utils.logger import get_logger

logger = get_logger(__name__)


async def not_taken_reminder_job(instance_id: int, bot, session_factory) -> None:
    async with session_factory() as session:
        repo = TaskRepository(session)
        inst = await repo.get_instance(instance_id)
        if inst is None or inst.status != TaskStatus.CREATED:
            return
        settings = SettingService(session)
        hours = int(await settings.get("reminders.not_taken_after_hours"))
        if inst.responsible_telegram_id_snapshot is not None:
            who = mention(inst.responsible_telegram_id_snapshot,
                          inst.responsible_name_snapshot)
        elif inst.responsible_name_snapshot:
            who = html_escape(inst.responsible_name_snapshot)
        else:
            who = "—"
        text = (f"⏰ Задача {bold(inst.title_snapshot)} всё ещё не взята в работу "
                f"({hours}ч). Ответственный: {who}")
        chat_id = int(await settings.get("general.group_chat_id"))
        try:
            await bot.send_message(chat_id=chat_id,
                                   message_thread_id=inst.topic_snapshot, text=text)
        except Exception as exc:                      # noqa: BLE001 — не рушим job
            logger.warning("Not-taken reminder send failed for instance=%s: %s",
                           instance_id, exc)
