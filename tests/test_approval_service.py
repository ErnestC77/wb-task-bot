from datetime import datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from bot.database.models import Role, TaskStatus
from bot.database.repositories.task_repository import TaskRepository
from bot.database.repositories.user_repository import UserRepository
from bot.services.approval_service import ApprovalService, auto_approve_job


async def seed(session_factory):
    async with session_factory() as s:
        users = UserRepository(s)
        valya = await users.upsert(telegram_id=10, name="Валя", role=Role.MANAGER_WB)
        owner = await users.upsert(telegram_id=1, name="O", role=Role.OWNER)
        repo = TaskRepository(s)
        cfg = await repo.upsert_config(dict(
            external_task_id="t", title="Проверка", scenario="article_check",
            schedule_type="every_n_days", schedule_interval=2,
            responsible_user_id=valya.id, need_approval=True, is_active=True))
        inst = await repo.create_instance_idempotent(
            cfg, datetime(2026, 7, 10, 9), None,
            dict(title_snapshot="Проверка", scenario_snapshot="article_check",
                 need_approval_snapshot=True, approval_timeout_hours_snapshot=24,
                 responsible_name_snapshot="Валя"))
        await repo.transition_status(inst.id, [TaskStatus.CREATED],
                                     TaskStatus.IN_PROGRESS, valya.id, "btn:start")
        await s.commit()
        return inst.id, valya.id, owner.id


async def test_request_approval_sets_24h_deadline(session, session_factory):
    inst_id, valya_id, _ = await seed(session_factory)
    async with session_factory() as s:
        valya = await UserRepository(s).get_by_id(valya_id)
        svc = ApprovalService(s, AsyncMock())
        got = await svc.request_approval(await TaskRepository(s).get_instance(inst_id), valya)
        await s.commit()
        assert got.status == TaskStatus.WAITING_APPROVAL
        delta = got.approval_deadline_at - datetime.utcnow()
        assert timedelta(hours=23) < delta <= timedelta(hours=24)   # snapshot 24ч, не 2ч


async def test_auto_approve_only_from_waiting(session_factory):
    inst_id, valya_id, _ = await seed(session_factory)
    bot = AsyncMock()
    await auto_approve_job(inst_id, bot, session_factory)           # ещё in_progress
    async with session_factory() as s:
        assert (await TaskRepository(s).get_instance(inst_id)).status == TaskStatus.IN_PROGRESS

    async with session_factory() as s:
        valya = await UserRepository(s).get_by_id(valya_id)
        svc = ApprovalService(s, AsyncMock())
        await svc.request_approval(await TaskRepository(s).get_instance(inst_id), valya)
        await s.commit()
    await auto_approve_job(inst_id, bot, session_factory)
    async with session_factory() as s:
        inst = await TaskRepository(s).get_instance(inst_id)
        assert inst.status == TaskStatus.AUTO_APPROVED and inst.auto_approved_at


async def test_approve_and_return_to_work(session_factory):
    inst_id, valya_id, owner_id = await seed(session_factory)
    async with session_factory() as s:
        users = UserRepository(s)
        valya, owner = await users.get_by_id(valya_id), await users.get_by_id(owner_id)
        svc = ApprovalService(s, AsyncMock())
        await svc.request_approval(await TaskRepository(s).get_instance(inst_id), valya)
        # менеджер подтверждать не может
        with pytest.raises(PermissionError):
            await svc.approve(inst_id, valya)
        # возврат в работу требует комментария
        with pytest.raises(ValueError):
            await svc.return_to_work(inst_id, owner, comment="")
        got = await svc.return_to_work(inst_id, owner, comment="Переделать остатки")
        assert got.status == TaskStatus.IN_PROGRESS
        # снова на подтверждение и approve owner-ом
        await svc.request_approval(await TaskRepository(s).get_instance(inst_id), valya)
        got = await svc.approve(inst_id, owner)
        await s.commit()
        assert got.status == TaskStatus.APPROVED and got.approved_at is not None


async def test_auto_approve_is_noop_if_owner_approved_first(session_factory):
    """Двойная защита от гонки: owner нажал ✅ чуть раньше, чем сработал
    auto_approve_job (например, job уже был вычитан из очереди планировщика
    к моменту клика, но выполняется после commit'а approve()). auto_approve_job
    должен молча выйти (conditional transition_status вернёт None), не
    перезаписывая уже подтверждённую задачу и не выставляя auto_approved_at.
    """
    inst_id, valya_id, owner_id = await seed(session_factory)
    async with session_factory() as s:
        users = UserRepository(s)
        valya, owner = await users.get_by_id(valya_id), await users.get_by_id(owner_id)
        svc = ApprovalService(s, AsyncMock())
        await svc.request_approval(await TaskRepository(s).get_instance(inst_id), valya)
        got = await svc.approve(inst_id, owner)
        await s.commit()
        assert got.status == TaskStatus.APPROVED

    bot = AsyncMock()
    await auto_approve_job(inst_id, bot, session_factory)   # опоздавший job

    async with session_factory() as s:
        inst = await TaskRepository(s).get_instance(inst_id)
        assert inst.status == TaskStatus.APPROVED           # не перезаписан
        assert inst.auto_approved_at is None                # auto-поле не тронуто
    bot.send_message.assert_not_called()                    # job ничего не отправил


async def test_auto_approve_is_noop_if_returned_to_work_first(session_factory):
    """Тот же сценарий гонки, но для «Вернуть в работу»: если возврат в работу
    произошёл раньше срабатывания auto_approve_job, задача не должна внезапно
    стать auto_approved — статус обязан остаться in_progress.
    """
    inst_id, valya_id, owner_id = await seed(session_factory)
    async with session_factory() as s:
        users = UserRepository(s)
        valya, owner = await users.get_by_id(valya_id), await users.get_by_id(owner_id)
        svc = ApprovalService(s, AsyncMock())
        await svc.request_approval(await TaskRepository(s).get_instance(inst_id), valya)
        got = await svc.return_to_work(inst_id, owner, comment="Не готово")
        await s.commit()
        assert got.status == TaskStatus.IN_PROGRESS

    bot = AsyncMock()
    await auto_approve_job(inst_id, bot, session_factory)   # опоздавший job

    async with session_factory() as s:
        inst = await TaskRepository(s).get_instance(inst_id)
        assert inst.status == TaskStatus.IN_PROGRESS
        assert inst.auto_approved_at is None


async def test_auto_approve_uses_snapshot_timeout_not_current_setting(session_factory):
    """approval_timeout_hours_snapshot (24, зафиксирован в seed()) должен
    определять дедлайн независимо от текущего значения настройки
    approval.timeout_hours на момент request_approval — снапшот задачи не
    должен "плавать" вслед за более поздним изменением глобальной настройки.
    """
    from bot.services.setting_service import SettingService

    inst_id, valya_id, _ = await seed(session_factory)
    async with session_factory() as s:
        # Меняем текущую настройку на заведомо другое значение уже ПОСЛЕ
        # того, как снапшот задачи был зафиксирован в seed() (24ч).
        await SettingService(s).set("approval.timeout_hours", 2, None)
        await s.commit()

    async with session_factory() as s:
        valya = await UserRepository(s).get_by_id(valya_id)
        svc = ApprovalService(s, AsyncMock())
        got = await svc.request_approval(await TaskRepository(s).get_instance(inst_id), valya)
        await s.commit()
        delta = got.approval_deadline_at - datetime.utcnow()
        # Если бы использовалась текущая настройка (2ч), delta была бы <= 2ч.
        assert timedelta(hours=23) < delta <= timedelta(hours=24)


async def test_request_approval_job_registration_deduplicates_stale_job(session_factory):
    """Регрессия по находкам Task 12-13: если job auto_approve:{id} уже стоит в
    очереди неcтартовавшего планировщика (например, оставшийся от recover_jobs
    после рестарта до того, как handler успел его убрать), request_approval не
    должен просто накапливать дубликаты через replace_existing=True — он обязан
    явно снять старый job (remove_job) перед регистрацией нового.
    """
    inst_id, valya_id, _ = await seed(session_factory)
    scheduler = AsyncIOScheduler()   # не стартован — как и в recover_jobs
    job_id = f"auto_approve:{inst_id}"
    scheduler.add_job(lambda: None, "date",
                      run_date=datetime.utcnow() + timedelta(hours=1), id=job_id)

    async with session_factory() as s:
        valya = await UserRepository(s).get_by_id(valya_id)
        svc = ApprovalService(s, AsyncMock(), scheduler=scheduler)
        got = await svc.request_approval(await TaskRepository(s).get_instance(inst_id), valya)
        await s.commit()
        assert got.status == TaskStatus.WAITING_APPROVAL

    jobs_with_id = [j for j in scheduler.get_jobs() if j.id == job_id]
    assert len(jobs_with_id) == 1
    assert jobs_with_id[0].func is auto_approve_job
