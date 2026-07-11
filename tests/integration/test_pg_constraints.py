from datetime import datetime

import pytest
from sqlalchemy.exc import IntegrityError

from bot.database.models import ArticleCheckSession, Role, TaskInstance
from bot.database.repositories.task_repository import TaskRepository
from bot.database.repositories.user_repository import UserRepository

pytestmark = pytest.mark.pg


async def test_task_instance_unique_enforced_by_postgres(pg_session):
    user = await UserRepository(pg_session).upsert(telegram_id=1, name="V",
                                                    role=Role.MANAGER_WB)
    cfg = await TaskRepository(pg_session).upsert_config(dict(
        external_task_id="t", title="t", schedule_type="every_n_days",
        schedule_interval=2, responsible_user_id=user.id, is_active=True))
    await pg_session.commit()
    ts = datetime(2026, 7, 10, 9, 0)
    pg_session.add(TaskInstance(config_id=cfg.id, scheduled_at=ts,
                                scheduled_date=ts.date(), schedule_key="k1",
                                title_snapshot="t"))
    await pg_session.commit()
    pg_session.add(TaskInstance(config_id=cfg.id, scheduled_at=ts,
                                scheduled_date=ts.date(), schedule_key="k2",
                                title_snapshot="t"))
    with pytest.raises(IntegrityError):
        await pg_session.commit()
    await pg_session.rollback()


async def test_article_check_session_unique_enforced(pg_session):
    user = await UserRepository(pg_session).upsert(telegram_id=1, name="V",
                                                    role=Role.MANAGER_WB)
    cfg = await TaskRepository(pg_session).upsert_config(dict(
        external_task_id="t2", title="t", schedule_type="daily",
        responsible_user_id=user.id, is_active=True))
    await pg_session.commit()
    ts = datetime(2026, 7, 10, 9, 0)
    inst = TaskInstance(config_id=cfg.id, scheduled_at=ts, scheduled_date=ts.date(),
                        schedule_key="k3", title_snapshot="t")
    pg_session.add(inst)
    await pg_session.flush()
    pg_session.add(ArticleCheckSession(task_instance_id=inst.id,
                                       responsible_user_id=user.id, batch_size=15))
    await pg_session.commit()
    pg_session.add(ArticleCheckSession(task_instance_id=inst.id,
                                       responsible_user_id=user.id, batch_size=15))
    with pytest.raises(IntegrityError):
        await pg_session.commit()
    await pg_session.rollback()


async def test_user_telegram_id_unique_enforced(pg_session):
    """Не в брифе явно, но UserRepository.upsert полагается на UNIQUE(telegram_id)
    для корректной работы ON CONFLICT-подобной логики — проверяем ограничение
    напрямую (в обход upsert) как регресс на будущие изменения схемы."""
    from bot.database.models import User
    pg_session.add(User(telegram_id=42, name="A", role=Role.MANAGER_WB))
    await pg_session.commit()
    pg_session.add(User(telegram_id=42, name="B", role=Role.PARTNER))
    with pytest.raises(IntegrityError):
        await pg_session.commit()
    await pg_session.rollback()


async def test_topic_key_unique_enforced(pg_session):
    from bot.database.models import Topic
    pg_session.add(Topic(topic_key="goods", topic_name="Товары"))
    await pg_session.commit()
    pg_session.add(Topic(topic_key="goods", topic_name="Товары (дубль)"))
    with pytest.raises(IntegrityError):
        await pg_session.commit()
    await pg_session.rollback()
