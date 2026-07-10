from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models import Role, User
from bot.database.repositories.audit_repository import AuditRepository
from bot.database.repositories.permission_repository import PermissionRepository
from bot.utils.permissions import PERMISSION_KEYS


class LastOwnerPermissionError(Exception):
    """Owner пытается снять с себя право управления без подтверждения."""


class PermissionService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = PermissionRepository(session)
        self.audit = AuditRepository(session)

    async def has_permission(self, user: User | None, key: str) -> bool:
        if key not in PERMISSION_KEYS:
            raise KeyError(f"Неизвестное право: {key}")
        if user is None or not user.is_active:
            return False
        if user.role == Role.OWNER:
            # owner имеет всё, если явно не отозвано (revoke с force)
            perms = {p.permission_key: p.is_allowed for p in await self.repo.get_for_user(user.id)}
            return perms.get(key, True)
        if user.role == Role.PARTNER:
            return await self.repo.has(user.id, key)
        return False

    async def grant(self, actor: User, target_user_id: int, key: str) -> None:
        if key not in PERMISSION_KEYS:
            raise KeyError(f"Неизвестное право: {key}")
        await self.repo.set_permission(target_user_id, key, True, actor.id)
        await self.audit.add(actor_user_id=actor.id, action="permission.grant",
                             entity_type="user", entity_id=str(target_user_id),
                             new_value_json=f'"{key}"')

    async def revoke(self, actor: User, target_user_id: int, key: str,
                     force: bool = False) -> None:
        if key not in PERMISSION_KEYS:
            raise KeyError(f"Неизвестное право: {key}")
        if actor.id == target_user_id and actor.role == Role.OWNER and not force:
            raise LastOwnerPermissionError(
                "Снятие права управления с самого себя требует отдельного подтверждения")
        await self.repo.set_permission(target_user_id, key, False, actor.id)
        await self.audit.add(actor_user_id=actor.id, action="permission.revoke",
                             entity_type="user", entity_id=str(target_user_id),
                             old_value_json=f'"{key}"')

    async def list_permissions(self, user_id: int) -> dict[str, bool]:
        rows = {p.permission_key: p.is_allowed for p in await self.repo.get_for_user(user_id)}
        return {key: rows.get(key, False) for key in sorted(PERMISSION_KEYS)}
