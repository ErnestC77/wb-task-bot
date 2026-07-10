from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models import User


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_telegram_id(self, telegram_id: int) -> User | None:
        return await self.session.scalar(
            select(User).where(User.telegram_id == telegram_id))

    async def get_by_id(self, user_id: int) -> User | None:
        return await self.session.scalar(
            select(User).where(User.id == user_id))

    async def get_active_by_role(self, role: str) -> list[User]:
        return list(await self.session.scalars(
            select(User).where(User.role == role, User.is_active.is_(True))))

    async def get_owners_and_partners(self) -> list[User]:
        return list(await self.session.scalars(
            select(User).where(
                User.role.in_(["owner", "partner"]),
                User.is_active.is_(True),
            )))

    async def get_all(self, include_inactive: bool = False) -> list[User]:
        stmt = select(User)
        if not include_inactive:
            stmt = stmt.where(User.is_active.is_(True))
        return list(await self.session.scalars(stmt))

    async def upsert(self, telegram_id: int, name: str, role: str,
                      username: str | None = None, is_active: bool = True) -> User:
        user = await self.get_by_telegram_id(telegram_id)
        if user is None:
            user = User(telegram_id=telegram_id, name=name, role=role,
                        username=username, is_active=is_active)
            self.session.add(user)
        else:
            user.name = name
            user.role = role
            user.username = username
            user.is_active = is_active
        await self.session.flush()
        return user

    async def deactivate(self, user_id: int) -> None:
        user = await self.get_by_id(user_id)
        if user is not None:
            user.is_active = False
            await self.session.flush()
