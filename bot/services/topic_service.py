"""Работа с темами Telegram-группы (message_thread_id)."""
from aiogram import Bot
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.repositories.topic_repository import TopicRepository
from bot.services.setting_service import SettingService


class TopicService:
    def __init__(self, session: AsyncSession, bot: Bot) -> None:
        self.repo = TopicRepository(session)
        self.settings = SettingService(session)
        self.bot = bot

    async def resolve_thread_id(self, topic_key: str) -> int | None:
        topic = await self.repo.get_by_key(topic_key)
        return topic.message_thread_id if topic and topic.is_active else None

    async def send_test_message(self, topic_key: str) -> bool:
        thread_id = await self.resolve_thread_id(topic_key)
        chat_id = int(await self.settings.get("general.group_chat_id"))
        try:
            msg = await self.bot.send_message(chat_id=chat_id, message_thread_id=thread_id,
                                              text="🔧 Тест темы: сообщение будет удалено")
            await self.bot.delete_message(chat_id=chat_id, message_id=msg.message_id)
            return True
        except Exception:                            # noqa: BLE001
            return False
