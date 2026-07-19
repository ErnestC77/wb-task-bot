"""Audit log для веб-админки (найдено при разборе инцидента 2026-07-19: между
07-17 и 07-19 кто-то активно правил задачи через webadmin, но ни одной записи
в admin_audit_log не осталось - webadmin, в отличие от Telegram /admin, не
логировал вообще ничего).

webadmin аутентифицирует по ОДНОМУ общему паролю на весь staff (webadmin/
auth.py) - в сессии нет никакого per-user id, поэтому actor_user_id здесь
всегда None (осознанное решение: логируем ЧТО изменилось и КОГДА, но не
"кто" - различить сотрудников по паролю невозможно без отдельной системы
идентификации). ip_or_source="webadmin" хотя бы отличает эти записи от
Telegram /admin в общем логе.
"""
from datetime import date, datetime
from datetime import time as time_

from bot.services.audit_service import AuditService

_JSON_UNSAFE = (date, datetime, time_)


def _jsonify(value: object) -> object:
    return value.isoformat() if isinstance(value, _JSON_UNSAFE) else value


def snapshot(obj, fields: list[str]) -> dict:
    """Плоский словарь полей объекта ДО правки (вызывать до upsert/setattr —
    репозитории мутируют переданный объект in-place, см. TaskRepository.
    upsert_config)."""
    return {f: _jsonify(getattr(obj, f, None)) for f in fields}


async def log_create(session, entity_type: str, entity_id: object, new_value: dict) -> None:
    await AuditService(session).log(
        None, f"{entity_type}.create", entity_type=entity_type, entity_id=str(entity_id),
        new_value={k: _jsonify(v) for k, v in new_value.items()}, ip_or_source="webadmin")


async def log_edit(session, entity_type: str, entity_id: object,
                   old_value: dict, new_value: dict) -> None:
    await AuditService(session).log(
        None, f"{entity_type}.edit", entity_type=entity_type, entity_id=str(entity_id),
        old_value={k: _jsonify(v) for k, v in old_value.items()},
        new_value={k: _jsonify(v) for k, v in new_value.items()}, ip_or_source="webadmin")
