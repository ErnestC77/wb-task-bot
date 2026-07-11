from datetime import date, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from bot.database.models import (
    Article, ArticleCategory, CheckStatus, DecisionType, ProblemType, Role, TaskStatus,
)
from bot.database.repositories.article_check_repository import ArticleCheckRepository
from bot.database.repositories.task_repository import TaskRepository
from bot.database.repositories.user_repository import UserRepository
from bot.services.approval_service import ApprovalService, auto_approve_job
from bot.services.article_check_service import ArticleCheckService
from bot.services.setting_service import SettingService


async def seed(session, n_articles=20):
    users = UserRepository(session)
    valya = await users.upsert(telegram_id=10, name="Валя", role=Role.MANAGER_WB)
    owner = await users.upsert(telegram_id=1, name="O", role=Role.OWNER)
    session.add_all([Article(article=f"{10000000 + i}", product_name=f"Товар {i}",
                             sort_order=i) for i in range(n_articles)])
    session.add_all([ArticleCategory(name="Тест"), ProblemType(name="высокий CPL"),
                     DecisionType(name="снизить ставку рекламы")])
    repo = TaskRepository(session)
    cfg = await repo.upsert_config(dict(
        external_task_id="articles_check_all", title="Проверка",
        scenario="article_check", schedule_type="every_n_days", schedule_interval=2,
        responsible_user_id=valya.id, need_approval=True, is_active=True))
    inst = await repo.create_instance_idempotent(
        cfg, datetime(2026, 7, 10, 9), datetime(2026, 7, 10, 12),
        dict(title_snapshot="Проверка", scenario_snapshot="article_check",
             article_batch_size_snapshot=15, need_approval_snapshot=True,
             approval_timeout_hours_snapshot=24, responsible_name_snapshot="Валя"))
    await session.commit()
    return inst, valya, owner


async def test_start_creates_snapshot_and_resumes(session):     # тесты 5, 13, 14
    inst, valya, _ = await seed(session)
    svc = ArticleCheckService(session)
    s1 = await svc.start_check(inst, valya)
    await session.commit()
    assert s1.total_articles == 20 and s1.batch_size == 15
    # добавление нового артикула НЕ меняет snapshot активной сессии (тест 14)
    session.add(Article(article="99999999", product_name="Новый"))
    await session.commit()
    s2 = await svc.start_check(inst, valya)          # повторный старт → та же сессия
    assert s2.id == s1.id and s2.total_articles == 20


async def test_only_responsible_marks(session):                 # тесты 6, 7
    inst, valya, owner = await seed(session)
    svc = ArticleCheckService(session)
    s = await svc.start_check(inst, valya)
    item = (await ArticleCheckRepository(session).get_items_for_batch(s.id, 1, 15))[0]
    with pytest.raises(PermissionError):
        await svc.mark(item.id, item.version, CheckStatus.CHECKED_NO_ACTION, owner)
    assert await svc.mark(item.id, item.version, CheckStatus.CHECKED_NO_ACTION, valya)


async def test_change_result_respects_setting(session):         # тест 4
    inst, valya, _ = await seed(session)
    svc = ArticleCheckService(session)
    s = await svc.start_check(inst, valya)
    chk = ArticleCheckRepository(session)
    item = (await chk.get_items_for_batch(s.id, 1, 15))[0]
    await svc.mark(item.id, 1, CheckStatus.CHECKED_NO_ACTION, valya)
    assert await svc.mark(item.id, 2, CheckStatus.ACTION_REQUIRED, valya)  # менять можно
    await SettingService(session).set("article_check.allow_change_result", False, valya.id)
    with pytest.raises(PermissionError):
        await svc.mark(item.id, 3, CheckStatus.CHECKED_NO_ACTION, valya)


async def test_cannot_finish_batch_with_pending(session):       # тест 3
    inst, valya, _ = await seed(session)
    svc = ArticleCheckService(session)
    s = await svc.start_check(inst, valya)
    assert await svc.can_finish_batch(s.id, 1) is False
    chk = ArticleCheckRepository(session)
    for item in await chk.get_items_for_batch(s.id, 1, 15):
        await svc.mark(item.id, item.version, CheckStatus.CHECKED_NO_ACTION, valya)
    assert await svc.can_finish_batch(s.id, 1) is True


async def test_finish_requires_actions_for_action_required(session):  # тесты 8, 11
    inst, valya, _ = await seed(session, n_articles=2)
    svc = ArticleCheckService(session)
    s = await svc.start_check(inst, valya)
    chk = ArticleCheckRepository(session)
    items = await chk.get_items_for_batch(s.id, 1, 15)
    await svc.mark(items[0].id, 1, CheckStatus.ACTION_REQUIRED, valya)
    await svc.mark(items[1].id, 1, CheckStatus.CHECKED_NO_ACTION, valya)
    ok, reason = await svc.finish_check(inst, valya)
    assert ok is False and "действ" in reason.lower()   # нет ArticleAction

    from sqlalchemy import select
    from bot.database.models import ArticleCategory, DecisionType, ProblemType
    cat = (await session.execute(select(ArticleCategory))).scalar_one()
    prob = (await session.execute(select(ProblemType))).scalar_one()
    dec = (await session.execute(select(DecisionType))).scalar_one()
    action = await svc.create_action(items[0].id, valya, cat.id, prob.id, dec.id,
                                     comment="снизить ставку",
                                     next_check_date=date(2026, 7, 13))
    assert action.problem_name_snapshot == "высокий CPL"
    ok, _ = await svc.finish_check(inst, valya)
    await session.commit()
    assert ok is True
    got = await TaskRepository(session).get_instance(inst.id)
    assert got.status == TaskStatus.WAITING_APPROVAL     # need_approval_snapshot=True


async def test_finish_check_routes_waiting_approval_through_approval_service(session):
    """Регрессия по находке ревьюера (Critical): finish_check при need_approval=True
    обязан идти через ApprovalService.request_approval, а не напрямую через
    TaskRepository.transition_status — иначе approval_deadline_at не выставляется
    и auto-approve job не регистрируется, задача зависает в waiting_approval навсегда.
    """
    inst, valya, _ = await seed(session, n_articles=2)
    scheduler = AsyncIOScheduler()   # не стартован, как и в тестах Task 15
    bot = AsyncMock()
    approval = ApprovalService(session, bot, scheduler=scheduler)
    svc = ArticleCheckService(session, bot=bot, approval_service=approval)
    s = await svc.start_check(inst, valya)
    chk = ArticleCheckRepository(session)
    for item in await chk.get_items_for_batch(s.id, 1, 15):
        await svc.mark(item.id, item.version, CheckStatus.CHECKED_NO_ACTION, valya)

    ok, reason = await svc.finish_check(inst, valya)
    await session.commit()
    assert ok is True, reason

    got = await TaskRepository(session).get_instance(inst.id)
    assert got.status == TaskStatus.WAITING_APPROVAL
    assert got.approval_deadline_at is not None            # ключевая проверка находки
    delta = got.approval_deadline_at - datetime.utcnow()
    assert timedelta(hours=23) < delta <= timedelta(hours=24)   # snapshot 24ч

    job = scheduler.get_job(f"auto_approve:{inst.id}")
    assert job is not None and job.func is auto_approve_job   # job реально зарегистрирован
    bot.send_message.assert_called()                          # подтверждающие уведомлены


async def test_finish_check_without_approval_refreshes_task_message(session):
    """Важная находка: ветка finish_check без подтверждения (need_approval=False)
    должна обновлять исходное Telegram-сообщение задачи, как это делают
    user_transition/request_approval/approve.
    """
    users = UserRepository(session)
    valya = await users.upsert(telegram_id=11, name="Валя2", role=Role.MANAGER_WB)
    session.add(Article(article="10000001", product_name="Товар"))
    repo = TaskRepository(session)
    cfg = await repo.upsert_config(dict(
        external_task_id="articles_check_no_approval", title="Проверка без подтверждения",
        scenario="article_check", schedule_type="every_n_days", schedule_interval=2,
        responsible_user_id=valya.id, need_approval=False, is_active=True))
    inst = await repo.create_instance_idempotent(
        cfg, datetime(2026, 7, 10, 9), datetime(2026, 7, 10, 12),
        dict(title_snapshot="Проверка без подтверждения", scenario_snapshot="article_check",
             article_batch_size_snapshot=15, need_approval_snapshot=False,
             approval_timeout_hours_snapshot=24, responsible_name_snapshot="Валя2",
             telegram_chat_id=-100123, telegram_message_id=777))
    await session.commit()

    bot = AsyncMock()
    svc = ArticleCheckService(session, bot=bot)
    s = await svc.start_check(inst, valya)
    chk = ArticleCheckRepository(session)
    for item in await chk.get_items_for_batch(s.id, 1, 15):
        await svc.mark(item.id, item.version, CheckStatus.CHECKED_NO_ACTION, valya)

    ok, reason = await svc.finish_check(inst, valya)
    assert ok is True, reason
    got = await TaskRepository(session).get_instance(inst.id)
    assert got.status == TaskStatus.COMPLETED
    bot.edit_message_text.assert_called_once()   # refresh_task_message вызван


async def test_next_batch_clamps_at_last_batch(session):
    inst, valya, _ = await seed(session, n_articles=20)   # batch_size=15 -> 2 пачки
    svc = ArticleCheckService(session)
    s = await svc.start_check(inst, valya)
    assert s.current_batch == 1
    b2 = await svc.next_batch(s.id)
    assert b2 == 2
    b_still_2 = await svc.next_batch(s.id)                # за границей — клэмп
    assert b_still_2 == 2


async def test_prev_batch_clamps_at_first_and_respects_setting(session):
    inst, valya, _ = await seed(session, n_articles=20)
    svc = ArticleCheckService(session)
    s = await svc.start_check(inst, valya)
    await svc.next_batch(s.id)
    b1 = await svc.prev_batch(s.id)
    assert b1 == 1
    b_still_1 = await svc.prev_batch(s.id)                # за границей — клэмп
    assert b_still_1 == 1

    await SettingService(session).set("article_check.allow_prev_batch", False, valya.id)
    with pytest.raises(PermissionError):
        await svc.prev_batch(s.id)
