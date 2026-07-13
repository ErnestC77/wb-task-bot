from datetime import datetime, time
from zoneinfo import ZoneInfo

import pytest

from bot.database.models import Role, TaskStatus
from bot.database.repositories.task_repository import TaskRepository
from bot.database.repositories.user_repository import UserRepository
from bot.services import task_service as task_service_module
from bot.services.setting_service import SettingService
from bot.services.task_service import TaskService


def _pin_now(monkeypatch, dt: datetime) -> None:
    """Детерминированно фиксирует now_tz внутри task_service, чтобы
    postpone_to_tomorrow не зависел от реального времени запуска тестов."""
    monkeypatch.setattr(task_service_module, "now_tz",
                        lambda tz_name: dt.replace(tzinfo=ZoneInfo(tz_name)))


async def make_config(session):
    users = UserRepository(session)
    valya = await users.upsert(telegram_id=10, name="Валя", role=Role.MANAGER_WB)
    cfg = await TaskRepository(session).upsert_config(dict(
        external_task_id="articles_check_all",
        title="Проверить все артикулы и выявить артикулы, по которым нужны действия",
        scenario="article_check", schedule_type="every_n_days", schedule_interval=2,
        time=time(9, 0), due_time=time(12, 0), responsible_user_id=valya.id,
        need_approval=True, is_active=True))
    await session.commit()
    return cfg, valya


async def test_snapshot_copies_settings(session):
    cfg, valya = await make_config(session)
    svc = TaskService(session)
    snap = await svc.build_snapshot(cfg)
    assert snap["approval_timeout_hours_snapshot"] == 24
    assert snap["article_batch_size_snapshot"] == 15
    assert snap["responsible_name_snapshot"] == "Валя"
    assert snap["scenario_snapshot"] == "article_check"


async def test_snapshot_frozen_after_setting_change(session):
    """Тесты 11, 13 (админ): изменение настроек не влияет на созданную задачу."""
    cfg, valya = await make_config(session)
    svc = TaskService(session)
    inst = await svc.create_instance_for(cfg, datetime(2026, 7, 10, 9, 0))
    await session.commit()
    settings = SettingService(session)
    await settings.set("approval.timeout_hours", 2, actor_user_id=valya.id)
    await settings.set("article_check.batch_size", 30, actor_user_id=valya.id)
    await session.commit()
    assert inst.approval_timeout_hours_snapshot == 24    # старая задача — 24ч
    assert inst.article_batch_size_snapshot == 15         # и пачка 15
    inst2 = await svc.create_instance_for(cfg, datetime(2026, 7, 12, 9, 0))
    assert inst2.approval_timeout_hours_snapshot == 2     # новая — по новым настройкам
    assert inst2.article_batch_size_snapshot == 30


async def test_resolve_responsible_prefers_explicit_assignment(session):
    cfg, valya = await make_config(session)          # responsible_user_id уже = valya
    cfg.responsible_role = "manager_wb"               # даже если роль тоже задана
    svc = TaskService(session)
    resolved = await svc.resolve_responsible_user(cfg)
    assert resolved.id == valya.id


async def test_resolve_responsible_by_role_when_unassigned(session):
    users = UserRepository(session)
    oksana = await users.upsert(telegram_id=20, name="Оксана", role=Role.LOGISTIC)
    cfg = await TaskRepository(session).upsert_config(dict(
        external_task_id="logistics_task", title="Логистика", scenario="simple",
        schedule_type="daily", responsible_role="logistic",
        responsible_user_id=None, is_active=True))
    await session.commit()
    svc = TaskService(session)
    resolved = await svc.resolve_responsible_user(cfg)
    assert resolved.id == oksana.id

    inst = await svc.create_instance_for(cfg, datetime(2026, 7, 10, 9, 0))
    assert inst.responsible_user_id == oksana.id
    assert inst.responsible_name_snapshot == "Оксана"


async def test_resolve_responsible_by_role_ambiguous_stays_unassigned(session):
    users = UserRepository(session)
    await users.upsert(telegram_id=21, name="Первый", role=Role.MANAGER_WB)
    await users.upsert(telegram_id=22, name="Второй", role=Role.MANAGER_WB)
    cfg = await TaskRepository(session).upsert_config(dict(
        external_task_id="ambiguous_task", title="Задача", scenario="simple",
        schedule_type="daily", responsible_role="manager_wb",
        responsible_user_id=None, is_active=True))
    await session.commit()
    svc = TaskService(session)
    resolved = await svc.resolve_responsible_user(cfg)
    assert resolved is None                            # два кандидата — неоднозначно


async def test_due_at_from_due_time(session):
    cfg, _ = await make_config(session)
    inst = await TaskService(session).create_instance_for(cfg, datetime(2026, 7, 10, 9, 0))
    assert inst.due_at == datetime(2026, 7, 10, 12, 0)    # «сегодня до 12:00»


async def test_only_responsible_can_transition(session):
    cfg, valya = await make_config(session)
    users = UserRepository(session)
    owner = await users.upsert(telegram_id=1, name="O", role=Role.OWNER)
    svc = TaskService(session)
    inst = await svc.create_instance_for(cfg, datetime(2026, 7, 10, 9, 0))
    await session.commit()
    with pytest.raises(PermissionError):
        await svc.user_transition(inst.id, [TaskStatus.CREATED],
                                  TaskStatus.IN_PROGRESS, owner, "btn:start")
    got = await svc.user_transition(inst.id, [TaskStatus.CREATED],
                                    TaskStatus.IN_PROGRESS, valya, "btn:start")
    assert got.status == TaskStatus.IN_PROGRESS


async def test_postpone_to_tomorrow(session, monkeypatch):
    _pin_now(monkeypatch, datetime(2026, 7, 10, 15, 0))
    cfg, valya = await make_config(session)
    svc = TaskService(session)
    inst = await svc.create_instance_for(cfg, datetime(2026, 7, 10, 9, 0))
    await session.commit()
    got = await svc.postpone_to_tomorrow(inst.id, valya)
    assert got.status == TaskStatus.POSTPONED and got.postponed_to.day == 11


async def test_postpone_to_tomorrow_after_several_days_uses_real_today(session, monkeypatch):
    """Task 14 fix: перенос может случиться через несколько дней после
    scheduled_date (задача простояла в overdue/reminder-цикле) — "завтра"
    должно считаться от реального текущего дня, а не от scheduled_date,
    иначе получаем дату в прошлом."""
    cfg, valya = await make_config(session)
    svc = TaskService(session)
    # Задача запланирована на 2026-07-10, но переносят её только на 3-й
    # день после этого — 2026-07-13.
    inst = await svc.create_instance_for(cfg, datetime(2026, 7, 10, 9, 0))
    await session.commit()
    _pin_now(monkeypatch, datetime(2026, 7, 13, 10, 0))
    got = await svc.postpone_to_tomorrow(inst.id, valya)
    assert got.status == TaskStatus.POSTPONED
    assert got.postponed_to == datetime(2026, 7, 14, 9, 0)   # реальное завтра
    assert got.postponed_to.date() > datetime(2026, 7, 13, 10, 0).date()  # не в прошлом
    # старая (сломанная) формула scheduled_date(07-10) + 1 день дала бы 07-11 — уже в прошлом
    assert got.postponed_to.date() != datetime(2026, 7, 11).date()
