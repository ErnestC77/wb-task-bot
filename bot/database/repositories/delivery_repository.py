from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models import DeliveryAttempt


class DeliveryRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add_attempt(self, entity_type: str, entity_id: int, chat_id: int | None,
                          thread_id: int | None, attempt_number: int, status: str,
                          error_text: str | None = None) -> DeliveryAttempt:
        attempt = DeliveryAttempt(
            entity_type=entity_type, entity_id=entity_id, chat_id=chat_id,
            message_thread_id=thread_id, attempt_number=attempt_number,
            status=status, error_text=error_text)
        self.session.add(attempt)
        await self.session.flush()
        return attempt

    async def attempts_for(self, entity_type: str, entity_id: int) -> list[DeliveryAttempt]:
        return list(await self.session.scalars(
            select(DeliveryAttempt).where(
                DeliveryAttempt.entity_type == entity_type,
                DeliveryAttempt.entity_id == entity_id)
            .order_by(DeliveryAttempt.attempt_number)))
