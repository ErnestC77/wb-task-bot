"""Task 38 Step 5 (продолжение test_scheduler_recovery_pg.py): полный сценарий
рестарта контейнера на реальном Postgres — повторный recover_jobs (как при
падении между стартом и scheduler.start()) не плодит дублей job'ов, и
эскалация неотвеченных вопросов (единственная категория recover_jobs, не
покрытая test_scheduler_recovery_pg.py) корректно находит "старые" записи
через `TaskQuestion.created_at < dt` — сравнение TIMESTAMP без TZ на
реальном диалекте, а не в SQLite, где типы не проверяются вовсе."""
from datetime import datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from bot.database.models import QuestionStatus, Role
from bot.database.repositories.question_repository import QuestionRepository
from bot.database.repositories.task_repository import TaskRepository
from bot.database.repositories.user_repository import UserRepository
from bot.services.scheduler_recovery_service import recover_jobs
from bot.services.setting_service import SettingService
from bot.services.task_service import TaskService

pytestmark = pytest.mark.pg


async def test_recover_is_idempotent_across_restarts_on_postgres(pg_session_factory):
    """Повторный recover_jobs (симулирует ретрай старта контейнера ДО
    scheduler.start()) не создаёт дублей — та же гарантия, что и в Task 13,
    здесь проверенная на реальной БД."""
    async with pg_session_factory() as s:
        user = await UserRepository(s).upsert(telegram_id=1, name="V", role=Role.MANAGER_WB)
        await TaskRepository(s).upsert_config(dict(
            external_task_id="restart_test", title="t", schedule_type="daily",
            responsible_user_id=user.id, is_active=True,
            next_run_at=datetime.utcnow() + timedelta(days=1)))
        await s.commit()

    scheduler = AsyncIOScheduler()
    await recover_jobs(scheduler, AsyncMock(), pg_session_factory)
    first = len(scheduler.get_jobs())
    await recover_jobs(scheduler, AsyncMock(), pg_session_factory)
    assert len(scheduler.get_jobs()) == first


async def test_question_escalation_recovered_with_real_timestamp_comparison(pg_session_factory):
    """`QuestionRepository.unanswered_older_than` фильтрует по `status`, а не
    по возрасту (единственный вызывающий код — recover_jobs — всегда передаёт
    "сейчас": каждый ещё не закрытый вопрос получает job эскалации заново,
    просто с разным run_date в зависимости от того, сколько времени уже
    прошло с его created_at — `_not_past` клампит просроченные к "почти
    сразу"). Проверяем на реальном Postgres: (1) TIMESTAMP-сравнение
    `created_at < dt` не падает и не теряет старую запись; (2) статус-фильтр
    (`not_in(["answered","closed"])`) реально исключает уже отвеченный
    вопрос из восстановления."""
    async with pg_session_factory() as s:
        asker = await UserRepository(s).upsert(telegram_id=10, name="Asker", role=Role.MANAGER_WB)
        receiver = await UserRepository(s).upsert(telegram_id=11, name="Receiver", role=Role.OWNER)
        cfg = await TaskRepository(s).upsert_config(dict(
            external_task_id="q_esc", title="t", schedule_type="daily",
            responsible_user_id=asker.id, is_active=True))
        inst = await TaskService(s).create_instance_for(cfg, datetime.utcnow())
        await SettingService(s).set("questions.escalation_hours", 4, asker.id)
        # created_at backdated явно (переопределяет server_default=func.now())
        old_ts = datetime.utcnow() - timedelta(hours=10)
        pending = await QuestionRepository(s).create(
            task_instance_id=inst.id, from_user_id=asker.id, to_user_id=receiver.id,
            question_text="?", status=QuestionStatus.SENT, created_at=old_ts)
        # уже отвеченный вопрос — НЕ должен попасть в эскалацию (статус-фильтр)
        await QuestionRepository(s).create(
            task_instance_id=inst.id, from_user_id=asker.id, to_user_id=receiver.id,
            question_text="??", status=QuestionStatus.ANSWERED,
            answer_text="ok", created_at=old_ts)
        await s.commit()
        pending_id = pending.id

    scheduler = AsyncIOScheduler()
    counters = await recover_jobs(scheduler, AsyncMock(), pg_session_factory)
    assert counters["questions"] == 1

    escalation_jobs = [j for j in scheduler.get_jobs() if j.id.startswith("question_escalation:")]
    assert len(escalation_jobs) == 1
    assert escalation_jobs[0].id == f"question_escalation:{pending_id}"
