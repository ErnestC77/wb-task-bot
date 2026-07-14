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
    Он должен ТОЧНО (96 ключей) совпадать по составу, дефолтам, типам и категориям
    с тем, что вставляет seed-миграция 0002_seed_defaults.py."""
    seed = _load_seed_defaults()
    assert len(seed) == 96
    assert len(SETTINGS_REGISTRY) == 96

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
        "questions": 8, "reports": 14, "sync": 8, "internal": 1,
        "delivery_log": 2, "status_notifications": 4, "status_history_log": 2,
    }


async def test_delivery_log_settings_registered_with_defaults(session):
    """Часть Б: настройки журнала отправок — обычные карточки в общем
    реестре, категория delivery_log (не sync)."""
    from bot.services.setting_service import SETTINGS_REGISTRY, SettingService

    svc = SettingService(session)
    assert await svc.get("delivery_log.enabled") is False
    assert await svc.get("delivery_log.interval_minutes") == 60
    d = SETTINGS_REGISTRY["delivery_log.interval_minutes"]
    assert (d.min_, d.max_, d.category) == (5, 1440, "delivery_log")
    assert SETTINGS_REGISTRY["delivery_log.enabled"].category == "delivery_log"


def test_delivery_log_category_has_title():
    from bot.keyboards.admin.settings import CATEGORY_TITLES
    assert CATEGORY_TITLES["delivery_log"] == "Журнал отправок"


async def test_status_notifications_settings_registered_with_defaults(session):
    """Часть Г: настройки уведомлений о статусах — обычные карточки в общем
    реестре, отдельная категория status_notifications. Список уведомляемых
    статусов — настройка с default из 4 статусов (спека, ред. a3e6de6)."""
    from bot.services.setting_service import SETTINGS_REGISTRY, SettingService

    svc = SettingService(session)
    assert await svc.get("status_notifications.enabled") is False
    assert await svc.get("status_notifications.targets") == ["owner"]
    assert await svc.get("status_notifications.interval_minutes") == 5
    assert await svc.get("status_notifications.statuses") == [
        "in_progress", "completed", "problem", "overdue"]
    d = SETTINGS_REGISTRY["status_notifications.interval_minutes"]
    assert (d.min_, d.max_, d.category) == (1, 60, "status_notifications")
    assert SETTINGS_REGISTRY["status_notifications.statuses"].value_type is object
    assert SETTINGS_REGISTRY["status_notifications.enabled"].category == "status_notifications"


def test_status_notifications_category_has_title():
    from bot.keyboards.admin.settings import CATEGORY_TITLES
    assert CATEGORY_TITLES["status_notifications"] == "Уведомления о статусах"


async def test_status_history_log_settings_registered_with_defaults(session):
    """Часть Д: настройки выгрузки истории статусов — обычные карточки,
    отдельная категория status_history_log (не sync и не delivery_log)."""
    from bot.services.setting_service import SETTINGS_REGISTRY, SettingService

    svc = SettingService(session)
    assert await svc.get("status_history_log.enabled") is False
    assert await svc.get("status_history_log.interval_minutes") == 60
    d = SETTINGS_REGISTRY["status_history_log.interval_minutes"]
    assert (d.min_, d.max_, d.category) == (5, 1440, "status_history_log")
    assert SETTINGS_REGISTRY["status_history_log.enabled"].category == "status_history_log"


def test_status_history_log_category_has_title():
    from bot.keyboards.admin.settings import CATEGORY_TITLES
    assert CATEGORY_TITLES["status_history_log"] == "История статусов (лист)"


def test_every_editable_setting_has_a_human_description():
    """Экран настроек показывает описание вместо сырого ключа — у КАЖДОЙ
    редактируемой настройки должно быть непустое человекочитаемое описание."""
    missing = [k for k, d in SETTINGS_REGISTRY.items()
              if d.is_editable and not d.description.strip()]
    assert missing == []


def test_receiver_user_id_settings_marked_for_name_resolution():
    """questions.*_receiver_user_id хранят users.id (не telegram_id) — карточка
    должна резолвить имя, а не показывать голое число. reports.private_receiver_ids
    хранит СЫРЫЕ telegram chat_id внешних адресатов (не users.id) — НЕ размечен."""
    for key in ("questions.default_receiver_user_id",
                "questions.fallback_receiver_user_id",
                "questions.escalation_receiver_user_id"):
        assert SETTINGS_REGISTRY[key].value_kind == "user_id", key
    assert SETTINGS_REGISTRY["reports.private_receiver_ids"].value_kind is None
