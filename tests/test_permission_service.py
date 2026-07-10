import pytest

from bot.database.models import Role
from bot.database.repositories.user_repository import UserRepository
from bot.services.permission_service import LastOwnerPermissionError, PermissionService


async def _users(session):
    repo = UserRepository(session)
    owner = await repo.upsert(telegram_id=1, name="O", role=Role.OWNER)
    partner = await repo.upsert(telegram_id=2, name="P", role=Role.PARTNER)
    manager = await repo.upsert(telegram_id=3, name="M", role=Role.MANAGER_WB)
    return owner, partner, manager


async def test_owner_has_all_partner_granular(session):
    owner, partner, manager = await _users(session)
    svc = PermissionService(session)
    assert await svc.has_permission(owner, "settings.manage")
    assert not await svc.has_permission(partner, "settings.manage")
    await svc.grant(owner, partner.id, "settings.manage")
    assert await svc.has_permission(partner, "settings.manage")   # тест 25: сразу
    assert not await svc.has_permission(manager, "settings.manage")


async def test_unknown_permission_key_rejected(session):
    owner, partner, _ = await _users(session)
    svc = PermissionService(session)
    with pytest.raises(KeyError):
        await svc.grant(owner, partner.id, "root.everything")


async def test_owner_cannot_drop_own_last_admin_right(session):   # тест 30
    owner, _, _ = await _users(session)
    svc = PermissionService(session)
    with pytest.raises(LastOwnerPermissionError):
        await svc.revoke(owner, owner.id, "settings.manage")
    await svc.revoke(owner, owner.id, "settings.manage", force=True)  # с подтверждением можно
