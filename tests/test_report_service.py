from datetime import date, datetime, timedelta
from unittest.mock import AsyncMock

from bot.database.models import ArticleAction, Role, TaskStatus
from bot.database.repositories.task_repository import TaskRepository
from bot.database.repositories.topic_repository import TopicRepository
from bot.database.repositories.user_repository import UserRepository
from bot.services.report_service import ReportService, weekly_report_job
from bot.services.setting_service import SettingService
from tests.test_article_check_service import seed


async def test_metrics_and_render(session):
    inst, valya, _ = await seed(session)
    repo = TaskRepository(session)
    await repo.transition_status(inst.id, [TaskStatus.CREATED],
                                 TaskStatus.IN_PROGRESS, valya.id, "x")
    await repo.transition_status(inst.id, [TaskStatus.IN_PROGRESS],
                                 TaskStatus.WAITING_APPROVAL, valya.id, "x")
    await repo.transition_status(inst.id, [TaskStatus.WAITING_APPROVAL],
                                 TaskStatus.AUTO_APPROVED, None, "auto:approve")
    session.add(ArticleAction(task_instance_id=inst.id, user_id=valya.id,
                              article="10000001", problem_name_snapshot="высокий CPL",
                              decision_name_snapshot="снизить ставку рекламы",
                              status="open", next_check_date=date(2020, 1, 1)))
    await session.commit()

    svc = ReportService(session)
    data = await svc.collect_metrics(datetime.utcnow() - timedelta(days=7),
                                     datetime.utcnow() + timedelta(days=1))
    assert data.total == 1 and data.completed == 1 and data.auto_approved == 1
    assert data.completion_pct == 100
    assert len(data.expired_checks) == 1              # просроченная дата проверки

    text = await svc.render(data, "full")
    assert "Всего задач: 1" in text and "высокий CPL" in text
    assert "Валя" in text                             # статистика по сотруднику


async def test_render_escapes_html_in_dynamic_values(session):
    inst, valya, _ = await seed(session)
    session.add(ArticleAction(
        task_instance_id=inst.id, user_id=valya.id, article="10000001",
        problem_name_snapshot="<b>CPL</b>", decision_name_snapshot="<i>снизить</i>",
        comment="<script>x</script>", status="open", next_check_date=date(2020, 1, 1)))
    await session.commit()
    svc = ReportService(session)
    data = await svc.collect_metrics(datetime.utcnow() - timedelta(days=7),
                                     datetime.utcnow() + timedelta(days=1))
    text = await svc.render(data, "full")
    assert "<script>" not in text and "&lt;script&gt;" in text
    assert "<b>CPL</b>" not in text


async def test_render_short_omits_detail_sections(session):
    inst, valya, _ = await seed(session)
    session.add(ArticleAction(task_instance_id=inst.id, user_id=valya.id,
                              article="10000001", problem_name_snapshot="высокий CPL",
                              status="open", next_check_date=date(2020, 1, 1)))
    await session.commit()
    svc = ReportService(session)
    data = await svc.collect_metrics(datetime.utcnow() - timedelta(days=7),
                                     datetime.utcnow() + timedelta(days=1))
    text = await svc.render(data, "short")
    assert "Всего задач" in text
    assert "высокий CPL" not in text and "Валя" not in text


async def test_render_respects_show_flags_disabled(session):
    inst, valya, _ = await seed(session)
    session.add(ArticleAction(task_instance_id=inst.id, user_id=valya.id,
                              article="10000001", problem_name_snapshot="высокий CPL",
                              status="open", next_check_date=date(2020, 1, 1)))
    await session.commit()
    await SettingService(session).set("reports.show_problem_articles", False, None)
    await SettingService(session).set("reports.show_expired_checks", False, None)
    await SettingService(session).set("reports.show_per_employee", False, None)
    svc = ReportService(session)
    data = await svc.collect_metrics(datetime.utcnow() - timedelta(days=7),
                                     datetime.utcnow() + timedelta(days=1))
    text = await svc.render(data, "full")
    assert "высокий CPL" not in text
    assert "Валя" not in text


async def test_collect_metrics_scoped_to_employee(session):
    """/report для не-owner/partner — «свой отчёт» (employee_id filter)."""
    inst, valya, owner = await seed(session)
    repo = TaskRepository(session)
    await repo.transition_status(inst.id, [TaskStatus.CREATED],
                                 TaskStatus.IN_PROGRESS, valya.id, "x")
    kirill = await UserRepository(session).upsert(telegram_id=999, name="Кирилл",
                                                   role=Role.LOGISTIC)
    cfg2 = await repo.upsert_config(dict(
        external_task_id="t_kirill_report", title="Задача Кирилла", scenario="simple",
        schedule_type="daily", responsible_user_id=kirill.id, is_active=True))
    await repo.create_instance_idempotent(
        cfg2, datetime.utcnow(), None,
        dict(title_snapshot="Задача Кирилла", scenario_snapshot="simple",
             responsible_name_snapshot="Кирилл"))
    await session.commit()

    svc = ReportService(session)
    start, end = datetime.utcnow() - timedelta(days=7), datetime.utcnow() + timedelta(days=1)
    full = await svc.collect_metrics(start, end)
    assert full.total == 2
    scoped = await svc.collect_metrics(start, end, employee_id=valya.id)
    assert scoped.total == 1
    assert list(scoped.per_employee.keys()) == ["Валя"]


async def test_weekly_report_job_sends_to_topic_and_private_receivers(session_factory):
    async with session_factory() as s:
        await TopicRepository(s).upsert("reports", "Отчёты", message_thread_id=77)
        await SettingService(s).set("reports.private_receiver_ids", [555, 556], None)
        await SettingService(s).set("general.group_chat_id", -100123, None)
        await s.commit()
    bot = AsyncMock()
    await weekly_report_job(bot, session_factory)
    assert bot.send_message.await_count == 3           # topic + 2 private
    calls = bot.send_message.await_args_list
    topic_call = calls[0]
    assert topic_call.kwargs["chat_id"] == -100123
    assert topic_call.kwargs["message_thread_id"] == 77
    private_chat_ids = {c.kwargs["chat_id"] for c in calls[1:]}
    assert private_chat_ids == {555, 556}


async def test_weekly_report_job_survives_send_failure(session_factory):
    async with session_factory() as s:
        await SettingService(s).set("reports.private_receiver_ids", [555, 556], None)
        await s.commit()
    bot = AsyncMock()
    bot.send_message.side_effect = [Exception("boom"), None, None]
    await weekly_report_job(bot, session_factory)      # не должно бросать
    assert bot.send_message.await_count == 3
