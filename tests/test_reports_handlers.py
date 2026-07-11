from datetime import date
from unittest.mock import AsyncMock

from bot.database.models import ArticleAction
from bot.database.repositories.task_repository import TaskRepository
from bot.database.repositories.user_repository import UserRepository
from bot.handlers.reports import cmd_report
from tests.test_article_check_service import seed


async def _seed_with_problem(session):
    inst, valya, owner = await seed(session)
    session.add(ArticleAction(task_instance_id=inst.id, user_id=valya.id,
                              article="10000001", problem_name_snapshot="высокий CPL",
                              status="open", next_check_date=date(2020, 1, 1)))
    await session.commit()
    return inst, valya, owner


async def test_report_unregistered_user_rejected(session):
    await _seed_with_problem(session)
    message = AsyncMock()
    message.from_user.id = 424242
    await cmd_report(message, session)
    message.answer.assert_awaited()
    text = message.answer.await_args.args[0]
    assert "прав" in text.lower()


async def test_report_owner_sees_full_report_including_other_employee_problems(session):
    inst, valya, owner = await _seed_with_problem(session)
    message = AsyncMock()
    message.from_user.id = owner.telegram_id
    await cmd_report(message, session)
    text = message.answer.await_args.args[0]
    assert "Валя" in text and "высокий CPL" in text


async def test_report_employee_sees_only_own_scoped_report(session):
    inst, valya, owner = await _seed_with_problem(session)
    kirill = await UserRepository(session).upsert(telegram_id=321, name="Кирилл",
                                                   role="logistic")
    repo = TaskRepository(session)
    cfg2 = await repo.upsert_config(dict(
        external_task_id="t_kirill_report_h", title="Задача Кирилла", scenario="simple",
        schedule_type="daily", responsible_user_id=kirill.id, is_active=True))
    from datetime import datetime
    await repo.create_instance_idempotent(
        cfg2, datetime.utcnow(), None,
        dict(title_snapshot="Задача Кирилла", scenario_snapshot="simple",
             responsible_name_snapshot="Кирилл"))
    await session.commit()

    message = AsyncMock()
    message.from_user.id = valya.telegram_id
    await cmd_report(message, session)
    text = message.answer.await_args.args[0]
    assert "высокий CPL" in text                        # своя проблема видна
    assert "Кирилл" not in text                          # чужая статистика скрыта
