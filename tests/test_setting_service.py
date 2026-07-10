import importlib.util
import json
from pathlib import Path

import pytest
from sqlalchemy import select

from bot.database.models import AdminAuditLog, Role
from bot.database.repositories.user_repository import UserRepository
from bot.services.setting_service import SETTINGS_REGISTRY, SettingService


async def _owner(session):
    return await UserRepository(session).upsert(telegram_id=1, name="O", role=Role.OWNER)


async def test_defaults_from_registry(session):
    svc = SettingService(session)
    assert await svc.get_typed("approval.timeout_hours", int) == 24
    assert await svc.get_typed("article_check.batch_size", int) == 15


async def test_set_valid_value_and_audit(session):          # тесты 4 и 7
    owner = await _owner(session)
    svc = SettingService(session)
    await svc.set("approval.timeout_hours", 48, actor_user_id=owner.id)
    await session.commit()
    assert await svc.get_typed("approval.timeout_hours", int) == 48
    logs = list(await session.scalars(select(AdminAuditLog)))
    assert any(l.setting_key == "approval.timeout_hours" and l.new_value_json == "48"
               for l in logs)


async def test_set_cyrillic_value_audit_not_escaped(session):
    owner = await _owner(session)
    svc = SettingService(session)
    value = "Тестовое значение с кириллицей"
    await svc.set("sync.sheet_users", value, actor_user_id=owner.id)
    await session.commit()
    logs = list(await session.scalars(select(AdminAuditLog)))
    log = next(l for l in logs if l.setting_key == "sync.sheet_users")
    assert json.loads(log.new_value_json) == value
    assert "\\u" not in log.new_value_json


async def test_wrong_type_rejected(session):                 # тест 5
    owner = await _owner(session)
    svc = SettingService(session)
    with pytest.raises(ValueError):
        await svc.set("approval.timeout_hours", "не число", actor_user_id=owner.id)


async def test_unknown_key_rejected(session):                # тест 6
    owner = await _owner(session)
    svc = SettingService(session)
    with pytest.raises(KeyError):
        await svc.set("hacker.key", 1, actor_user_id=owner.id)
    with pytest.raises(KeyError):
        await svc.get("hacker.key")


async def test_bounds_and_choices(session):
    owner = await _owner(session)
    svc = SettingService(session)
    with pytest.raises(ValueError):
        await svc.set("approval.timeout_hours", 0, actor_user_id=owner.id)      # min 1
    with pytest.raises(ValueError):
        await svc.set("sync.conflict_policy", "chaos", actor_user_id=owner.id)  # choices


async def test_import_rejects_secret_and_unknown(session):   # тест 20
    owner = await _owner(session)
    svc = SettingService(session)
    report = await svc.import_settings(
        {"approval.timeout_hours": 12, "BOT_TOKEN": "x", "internal.settings_version": 99},
        actor_user_id=owner.id, dry_run=False)
    assert report["applied"] == ["approval.timeout_hours"]
    assert set(report["rejected"]) == {"BOT_TOKEN", "internal.settings_version"}
    assert await svc.get_typed("approval.timeout_hours", int) == 12


async def test_settings_version_increments(session):
    owner = await _owner(session)
    svc = SettingService(session)
    v1 = await svc.current_version()
    await svc.set("general.page_size", 20, actor_user_id=owner.id)
    assert await svc.current_version() == v1 + 1


def test_registry_covers_required_categories():
    cats = {d.category for d in SETTINGS_REGISTRY.values()}
    assert {"general", "article_check", "approval", "reminders",
            "questions", "reports", "sync", "internal"} <= cats


def _load_seed_defaults():
    """Загружает SETTINGS_DEFAULTS из seed-миграции 0002 напрямую по пути файла,
    т.к. имя модуля начинается с цифры и не импортируется обычным import."""
    path = (Path(__file__).resolve().parents[1] / "bot" / "database" / "migrations"
            / "versions" / "0002_seed_defaults.py")
    spec = importlib.util.spec_from_file_location("seed_defaults_0002", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.SETTINGS_DEFAULTS


_TYPE_MAP = {"int": int, "bool": bool, "str": str, "json": object}


def test_registry_matches_seed_migration_exactly():
    """SETTINGS_REGISTRY — единственный источник истины для допустимых ключей.
    Он должен ТОЧНО (86 ключей) совпадать по составу, дефолтам, типам и категориям
    с тем, что вставляет seed-миграция 0002_seed_defaults.py."""
    seed = _load_seed_defaults()
    assert len(seed) == 86
    assert len(SETTINGS_REGISTRY) == 86

    seed_keys = {key for key, _, _, _ in seed}
    assert set(SETTINGS_REGISTRY.keys()) == seed_keys

    for key, default, value_type, category in seed:
        d = SETTINGS_REGISTRY[key]
        assert d.category == category, f"{key}: категория расходится"
        assert d.default == default, f"{key}: default расходится"
        assert d.value_type == _TYPE_MAP[value_type], f"{key}: value_type расходится"

    counts_by_category: dict[str, int] = {}
    for key, _, _, category in seed:
        counts_by_category[category] = counts_by_category.get(category, 0) + 1
    assert counts_by_category == {
        "general": 14, "article_check": 19, "approval": 10, "reminders": 14,
        "questions": 8, "reports": 12, "sync": 8, "internal": 1,
    }
