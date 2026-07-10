from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models import AdminPermission


class PermissionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_for_user(self, user_id: int) -> list[AdminPermission]:
        return list(await self.session.scalars(
            select(AdminPermission).where(AdminPermission.user_id == user_id)))

    async def set_permission(self, user_id: int, key: str, allowed: bool,
                             granted_by: int | None) -> AdminPermission:
        perm = await self.session.scalar(select(AdminPermission).where(
            AdminPermission.user_id == user_id,
            AdminPermission.permission_key == key))
        if perm is None:
            perm = AdminPermission(user_id=user_id, permission_key=key,
                                   is_allowed=allowed, granted_by_user_id=granted_by)
            self.session.add(perm)
        else:
            perm.is_allowed = allowed
            perm.granted_by_user_id = granted_by
        await self.session.flush()
        return perm

    async def has(self, user_id: int, key: str) -> bool:
        perm = await self.session.scalar(select(AdminPermission).where(
            AdminPermission.user_id == user_id,
            AdminPermission.permission_key == key))
        return bool(perm and perm.is_allowed)
