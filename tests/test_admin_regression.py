"""Task 36: недостающие пункты регрессионной матрицы раздела 14 (админ-панель).

Большая часть из 30 пунктов матрицы уже покрыта тестами Tasks 7/8/16-35 как
естественное следствие TDD-цикла соответствующей функциональности (см. таблицу
покрытия в task-36-brief.md). Здесь — только реально недостающие явные тесты:
№12 (пересборка job'ов после изменения approval.timeout_hours), №21
(расширенный тест пагинации) и №23 (чужой callback отклоняется).
"""
from datetime import datetime
from unittest.mock import AsyncMock

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from bot.database.models import Role
from bot.database.repositories.user_repository import UserRepository
from bot.services.scheduler_service import SchedulerService
from bot.services.setting_service import SettingService
from tests.test_task_service import make_config


async def test_auto_approve_setting_change_rebuilds_future_jobs(session_factory):  # тест 12
    """Изменение approval.timeout_hours само по себе не трогает существующие jobs
    (snapshot), но SchedulerService.rebuild_config_job гарантированно использует
    новое значение при следующем создании TaskInstance."""
    async with session_factory() as s:
        cfg, valya = await make_config(s)
        owner = await UserRepository(s).upsert(telegram_id=1, name="O", role=Role.OWNER)
        await SettingService(s).set("approval.timeout_hours", 6, owner.id)
        await s.commit()
        cfg_id = cfg.id
    scheduler = AsyncIOScheduler()
    svc = SchedulerService(scheduler, AsyncMock(), session_factory)
    await svc.rebuild_config_job(cfg_id)
    jobs = [j for j in scheduler.get_jobs() if j.id == f"config:{cfg_id}"]
    assert len(jobs) == 1                                # без дублей

    # regression: повторный rebuild (например, второе изменение настройки) не
    # создаёт дубль, а заменяет job (Task 12/13 lesson — явный remove_job).
    await svc.rebuild_config_job(cfg_id)
    jobs = [j for j in scheduler.get_jobs() if j.id == f"config:{cfg_id}"]
    assert len(jobs) == 1


async def test_callback_from_other_user_rejected(session):                # тест 23
    """Callback с чужим instance_id, но от неответственного пользователя,
    отклоняется на уровне сервиса (не только 'неизвестный пользователь')."""
    cfg, valya = await make_config(session)
    stranger = await UserRepository(session).upsert(telegram_id=999, name="Чужой",
                                                     role=Role.MANAGER_WB)
    from bot.services.task_service import TaskService
    svc = TaskService(session)
    inst = await svc.create_instance_for(cfg, datetime(2026, 7, 10, 9))
    await session.commit()
    import pytest
    with pytest.raises(PermissionError):
        await svc.user_transition(inst.id, ["created"], "in_progress",
                                  stranger, "btn:start")


# ---------------------------------------------------------------------------
# тест 21 (расширенный) — пагинация действительно режет на страницы и не
# теряет/не дублирует записи на границах страниц.
# ---------------------------------------------------------------------------

async def test_pagination_covers_all_entries_without_gaps_or_duplicates(session):
    """Запрашивает СТРОГО total_pages страниц (не «пока список не опустеет» —
    _show_list зажимает запрошенный номер страницы к последней существующей,
    поэтому список НИКОГДА не возвращается пустым, и наивный цикл «пока
    непусто» зациклился бы, повторно засчитывая последнюю страницу)."""
    from bot.handlers.admin.reports import handle_rep_callback
    from bot.handlers.admin.settings import category_keys
    from bot.keyboards.admin.reports import RepCb

    owner = await UserRepository(session).upsert(telegram_id=1, name="O", role=Role.OWNER)
    page_size = 5
    await SettingService(session).set("general.page_size", page_size, owner.id)
    await session.commit()

    total = len(category_keys("reports"))
    total_pages = -(-total // page_size)
    assert total_pages > 1                                  # тест должен реально пересекать границу

    seen_keys: list[str] = []
    for page in range(1, total_pages + 1):
        callback = AsyncMock()
        callback.from_user.id = owner.telegram_id
        await handle_rep_callback(callback, RepCb(a="list", p=page), session)
        kb = callback.message.edit_text.await_args.kwargs.get("reply_markup") \
            or callback.message.edit_text.await_args.args[1]
        row_labels = [row[0].text for row in kb.inline_keyboard
                     if row[0].callback_data.startswith("r:card")]
        seen_keys.extend(row_labels)

    assert len(seen_keys) == total
    assert len(set(seen_keys)) == total                     # без дублей между страницами


# ---------------------------------------------------------------------------
# Regression, найдено при работе над Task 33/36: apply_setting_input коммитит
# ДО пересборки job'а планировщика (см. Task 33 review fix). Task 33 покрыл
# только sync.interval_minutes; идентичный путь для reports.weekday/
# reports.time (тот же SCHEDULER_AFFECTING набор, тот же вызывающий код) не
# был явно протестирован ни в Task 32, ни в Task 33 — закрываем здесь.
# ---------------------------------------------------------------------------

async def test_edit_reports_time_via_generic_editor_rebuilds_job_with_fresh_value(
        session_factory):
    from bot.handlers.admin.reports import handle_rep_value_message
    from bot.handlers.admin.settings import category_keys
    from bot.states.admin_states import AdminStates

    async with session_factory() as s:
        owner = await UserRepository(s).upsert(telegram_id=1, name="O", role=Role.OWNER)
        await s.commit()
        owner_id = owner.id

    def _field(job, name):
        return next(f for f in job.trigger.fields if f.name == name)

    scheduler = AsyncIOScheduler()
    scheduler.wb_service = SchedulerService(scheduler, AsyncMock(), session_factory)
    await scheduler.wb_service.register_report_job()
    old_job = scheduler.get_job("weekly_report")
    assert old_job is not None
    assert str(_field(old_job, "hour")) == "20" and str(_field(old_job, "minute")) == "0"

    class FakeState:
        def __init__(self):
            self.data = {"setting_key": "reports.time", "idx": 0}
            self.state = AdminStates.waiting_rep_value

        async def get_data(self):
            return dict(self.data)

        async def clear(self):
            self.data = {}
            self.state = None

    class FakeDispatcher:
        workflow_data = {"scheduler": scheduler}

    async with session_factory() as s:
        owner = await UserRepository(s).get_by_id(owner_id)
        message = AsyncMock()
        message.from_user.id = owner.telegram_id
        message.text = "09:30"
        await handle_rep_value_message(message, s, FakeState(), dispatcher=FakeDispatcher())

    new_job = scheduler.get_job("weekly_report")
    assert new_job is not None
    assert str(_field(new_job, "hour")) == "9" and str(_field(new_job, "minute")) == "30"
