from datetime import datetime, timedelta

from aiogram import Bot
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models import DeliveryStatus, TaskInstance
from bot.database.repositories.delivery_repository import DeliveryRepository
from bot.database.repositories.task_repository import TaskRepository
from bot.services.setting_service import SettingService
from bot.utils.logger import get_logger

logger = get_logger(__name__)

DEFAULT_RETRY_DELAY_SECONDS = 300  # fallback, если general.telegram_retry_intervals пуст


class DeliveryService:
    def __init__(self, session: AsyncSession, bot: Bot, scheduler=None) -> None:
        self.session = session
        self.bot = bot
        self.scheduler = scheduler
        self.repo = DeliveryRepository(session)
        self.tasks = TaskRepository(session)
        self.settings = SettingService(session)

    async def send_task_message(self, inst: TaskInstance) -> bool:
        from bot.keyboards.task_keyboards import keyboard_for_status
        from bot.utils.message_templates import render_task_message

        if inst.delivery_status == DeliveryStatus.SENT:
            return True                                # уже доставлено — не дублируем
        chat_id = int(await self.settings.get("general.group_chat_id"))
        attempt = inst.delivery_attempts + 1
        try:
            msg = await self.bot.send_message(
                chat_id=chat_id, message_thread_id=inst.topic_snapshot,
                text=render_task_message(inst),
                reply_markup=keyboard_for_status(inst))
        except Exception as exc:                     # noqa: BLE001
            await self._register_failure(inst, attempt, chat_id, str(exc))
            return False
        inst.delivery_attempts = attempt
        inst.delivery_status = DeliveryStatus.SENT
        inst.message_sent_at = datetime.utcnow()
        inst.next_retry_at = None
        await self.tasks.set_message_info(inst.id, msg.chat.id, msg.message_id)
        inst.telegram_chat_id, inst.telegram_message_id = msg.chat.id, msg.message_id
        await self.repo.add_attempt("task_instance", inst.id, chat_id,
                                    inst.topic_snapshot, attempt, "sent")
        return True

    async def _register_failure(self, inst: TaskInstance, attempt: int,
                                chat_id: int, error: str) -> None:
        inst.delivery_attempts = attempt
        inst.last_delivery_error = error
        await self.repo.add_attempt("task_instance", inst.id, chat_id,
                                    inst.topic_snapshot, attempt, "failed", error)
        max_attempts = int(await self.settings.get("general.telegram_retry_count"))
        intervals = list(await self.settings.get("general.telegram_retry_intervals"))
        if attempt >= max_attempts:
            inst.delivery_status = DeliveryStatus.ABANDONED
            inst.next_retry_at = None
            logger.error("Delivery abandoned for instance=%s: %s", inst.id, error)
            return
        delay = (intervals[min(attempt - 1, len(intervals) - 1)] if intervals
                 else DEFAULT_RETRY_DELAY_SECONDS)
        inst.delivery_status = DeliveryStatus.RETRYING
        inst.next_retry_at = datetime.utcnow() + timedelta(seconds=delay)
        if self.scheduler is not None:
            self.scheduler.add_job(
                self.retry_task_delivery, "date", run_date=inst.next_retry_at,
                args=[inst.id], id=f"retry_delivery:{inst.id}:{attempt}",
                replace_existing=True, misfire_grace_time=3600)

    async def retry_task_delivery(self, instance_id: int) -> None:
        inst = await self.tasks.get_instance(instance_id)
        if inst is not None and inst.delivery_status == DeliveryStatus.RETRYING:
            await self.send_task_message(inst)
            await self.session.commit()

    async def send_private(self, user_telegram_id: int, text: str,
                           reply_markup=None) -> tuple[bool, str | None]:
        try:
            msg = await self.bot.send_message(chat_id=user_telegram_id, text=text,
                                              reply_markup=reply_markup)
            return True, str(msg.message_id)
        except Exception as exc:                     # noqa: BLE001
            logger.warning("Private send to %s failed: %s", user_telegram_id, exc)
            return False, str(exc)
