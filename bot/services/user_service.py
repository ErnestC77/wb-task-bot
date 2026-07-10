from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models import User
from bot.database.repositories.user_repository import UserRepository


class UserService:
    def __init__(self, session: AsyncSession) -> None:
        self.repo = UserRepository(session)

    async def get_actor(self, telegram_id: int) -> User | None:
        user = await self.repo.get_by_telegram_id(telegram_id)
        return user if user and user.is_active else None

    async def upsert_from_telegram(self, telegram_id: int, name: str, role: str,
                                   username: str | None = None) -> User:
        return await self.repo.upsert(telegram_id=telegram_id, name=name, role=role,
                                      username=username)

    async def mark_private_chat_available(self, telegram_id: int) -> None:
        user = await self.repo.get_by_telegram_id(telegram_id)
        if user is not None:
            user.private_chat_available = True
