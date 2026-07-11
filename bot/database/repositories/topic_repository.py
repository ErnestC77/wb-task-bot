from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models import Topic


class TopicRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_key(self, topic_key: str) -> Topic | None:
        return await self.session.scalar(
            select(Topic).where(Topic.topic_key == topic_key))

    async def get_by_id(self, topic_id: int) -> Topic | None:
        return await self.session.get(Topic, topic_id)

    async def get_all(self, include_inactive: bool = False) -> list[Topic]:
        stmt = select(Topic)
        if not include_inactive:
            stmt = stmt.where(Topic.is_active.is_(True))
        return list(await self.session.scalars(stmt))

    async def get_active_not_in(self, topic_keys: set[str]) -> list[Topic]:
        """Task 22: активные темы, отсутствующие в свежей выгрузке Sheets."""
        stmt = select(Topic).where(Topic.is_active.is_(True))
        if topic_keys:
            stmt = stmt.where(Topic.topic_key.not_in(topic_keys))
        return list(await self.session.scalars(stmt))

    async def upsert(self, topic_key: str, topic_name: str,
                     message_thread_id: int | None = None,
                     event_types: str | None = None,
                     is_active: bool = True) -> Topic:
        topic = await self.get_by_key(topic_key)
        if topic is None:
            topic = Topic(topic_key=topic_key, topic_name=topic_name,
                          message_thread_id=message_thread_id,
                          event_types=event_types, is_active=is_active)
            self.session.add(topic)
        else:
            topic.topic_name = topic_name
            topic.message_thread_id = message_thread_id
            topic.event_types = event_types
            topic.is_active = is_active
        await self.session.flush()
        return topic

    async def set_thread_id(self, topic_key: str, thread_id: int) -> None:
        topic = await self.get_by_key(topic_key)
        if topic is not None:
            topic.message_thread_id = thread_id
            await self.session.flush()
