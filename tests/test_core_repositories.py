import json

from bot.database.models import Role
from bot.database.repositories.audit_repository import AuditRepository
from bot.database.repositories.permission_repository import PermissionRepository
from bot.database.repositories.setting_repository import SettingRepository
from bot.database.repositories.user_repository import UserRepository


async def test_user_upsert_and_deactivate(session):
    repo = UserRepository(session)
    u1 = await repo.upsert(telegram_id=1, name="Иван", role=Role.MANAGER_WB)
    u2 = await repo.upsert(telegram_id=1, name="Иван П.", role=Role.LOGISTIC)
    await session.commit()
    assert u1.id == u2.id and u2.name == "Иван П."
    await repo.deactivate(u1.id)
    await session.commit()
    assert (await repo.get_by_id(u1.id)).is_active is False   # не удалён


async def test_permission_upsert_no_duplicates(session):
    users = UserRepository(session)
    owner = await users.upsert(telegram_id=1, name="O", role=Role.OWNER)
    partner = await users.upsert(telegram_id=2, name="P", role=Role.PARTNER)
    perms = PermissionRepository(session)
    await perms.set_permission(partner.id, "settings.manage", True, owner.id)
    await perms.set_permission(partner.id, "settings.manage", False, owner.id)
    await session.commit()
    rows = await perms.get_for_user(partner.id)
    assert len(rows) == 1 and rows[0].is_allowed is False
    assert await perms.has(partner.id, "settings.manage") is False


async def test_setting_upsert_and_audit(session):
    settings = SettingRepository(session)
    await settings.upsert("approval.timeout_hours", json.dumps(24), "int", "approval", None)
    await settings.upsert("approval.timeout_hours", json.dumps(48), "int", "approval", None)
    await session.commit()
    assert json.loads((await settings.get("approval.timeout_hours")).value_json) == 48

    audit = AuditRepository(session)
    await audit.add(actor_user_id=None, action="setting.set",
                    setting_key="approval.timeout_hours",
                    old_value_json="24", new_value_json="48")
    await session.commit()
    page = await audit.list_page(page=1, page_size=10)
    assert page[0].action == "setting.set"
