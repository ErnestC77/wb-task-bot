"""Task 38 Step 10: сквозной сценарий на реальном Postgres — owner меняет
approval.timeout_hours через SettingService, создаёт задачу (snapshot содержит
новое значение), меняет ещё раз, снова создаёт задачу (второй snapshot
отличается от первого) — регресс на "изменение настройки не влияет на уже
созданные TaskInstance задним числом" (Task 24/36), здесь на реальной БД."""
from datetime import datetime, timedelta

import pytest

from bot.database.models import Role
from bot.database.repositories.task_repository import TaskRepository
from bot.database.repositories.user_repository import UserRepository
from bot.services.setting_service import SettingService
from bot.services.task_service import TaskService

pytestmark = pytest.mark.pg


async def test_setting_change_does_not_retroactively_affect_existing_snapshot(pg_session):
    owner = await UserRepository(pg_session).upsert(telegram_id=1, name="O", role=Role.OWNER)
    user = await UserRepository(pg_session).upsert(telegram_id=2, name="V", role=Role.MANAGER_WB)
    cfg = await TaskRepository(pg_session).upsert_config(dict(
        external_task_id="snap_pg", title="t", schedule_type="daily",
        responsible_user_id=user.id, need_approval=True, is_active=True))
    await pg_session.commit()

    await SettingService(pg_session).set("approval.timeout_hours", 6, owner.id)
    await pg_session.commit()
    inst1 = await TaskService(pg_session).create_instance_for(cfg, datetime(2026, 7, 10, 9))
    await pg_session.commit()
    assert inst1.approval_timeout_hours_snapshot == 6

    await SettingService(pg_session).set("approval.timeout_hours", 12, owner.id)
    await pg_session.commit()
    inst2 = await TaskService(pg_session).create_instance_for(cfg, datetime(2026, 7, 11, 9))
    await pg_session.commit()
    assert inst2.approval_timeout_hours_snapshot == 12

    # первый инстанс НЕ изменился задним числом
    refreshed_inst1 = await TaskRepository(pg_session).get_instance(inst1.id)
    assert refreshed_inst1.approval_timeout_hours_snapshot == 6


async def test_setting_change_creates_audit_log_entry(pg_session):
    from sqlalchemy import select

    from bot.database.models import AdminAuditLog

    owner = await UserRepository(pg_session).upsert(telegram_id=3, name="O2", role=Role.OWNER)
    await pg_session.commit()

    await SettingService(pg_session).set("approval.timeout_hours", 8, owner.id)
    await pg_session.commit()

    logs = list(await pg_session.scalars(
        select(AdminAuditLog).where(AdminAuditLog.setting_key == "approval.timeout_hours")))
    assert any(l.new_value_json == "8" for l in logs)
