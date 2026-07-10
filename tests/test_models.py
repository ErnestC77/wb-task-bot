from datetime import datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
import pytest

from bot.database.models import (
    Article, ArticleCheckItem, ArticleCheckSession, CheckStatus, Role,
    SystemSetting, TaskConfig, TaskInstance, TaskStatus, Topic, User,
)


async def test_task_instance_unique_schedule(session):
    user = User(telegram_id=1, name="Валя", role=Role.MANAGER_WB)
    topic = Topic(topic_key="goods", topic_name="Управление товарами", message_thread_id=42)
    session.add_all([user, topic])
    await session.flush()
    cfg = TaskConfig(external_task_id="articles_check_all",
                     title="Проверить все артикулы", scenario="article_check",
                     schedule_type="every_n_days", schedule_interval=2,
                     responsible_user_id=user.id, topic_id=topic.id)
    session.add(cfg)
    await session.flush()
    ts = datetime(2026, 7, 10, 9, 0)
    session.add(TaskInstance(config_id=cfg.id, scheduled_at=ts,
                             scheduled_date=ts.date(), schedule_key=f"{cfg.id}:{ts.isoformat()}",
                             title_snapshot="Проверить все артикулы"))
    await session.commit()
    session.add(TaskInstance(config_id=cfg.id, scheduled_at=ts,
                             scheduled_date=ts.date(), schedule_key="dup",
                             title_snapshot="x"))
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()


async def test_check_session_unique_per_instance(session):
    user = User(telegram_id=2, name="M", role=Role.MANAGER_WB)
    session.add(user)
    await session.flush()
    cfg = TaskConfig(external_task_id="t", title="t", scenario="article_check",
                     schedule_type="daily")
    session.add(cfg)
    await session.flush()
    inst = TaskInstance(config_id=cfg.id, scheduled_at=datetime(2026, 7, 10, 9),
                        scheduled_date=datetime(2026, 7, 10).date(),
                        schedule_key="k1", title_snapshot="t")
    session.add(inst)
    await session.flush()
    s1 = ArticleCheckSession(task_instance_id=inst.id, responsible_user_id=user.id,
                             total_articles=0, batch_size=15)
    session.add(s1)
    await session.commit()
    s1_id = s1.id  # captured before rollback: Session.rollback() expires all
    # identity-mapped objects unconditionally (see SessionTransaction._restore_snapshot),
    # so a bare `s1.id` access afterwards would trigger an out-of-greenlet lazy load
    # and raise MissingGreenlet under AsyncSession.
    session.add(ArticleCheckSession(task_instance_id=inst.id,
                                    responsible_user_id=user.id,
                                    total_articles=0, batch_size=15))
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()

    item = ArticleCheckItem(check_session_id=s1_id, article_snapshot="12345678",
                            product_name_snapshot="Товар", sort_order=1)
    session.add(item)
    await session.commit()
    assert item.check_status == CheckStatus.PENDING and item.version == 1


async def test_setting_and_article_defaults(session):
    session.add_all([
        SystemSetting(key="approval.timeout_hours", value_json="24",
                      value_type="int", category="approval"),
        Article(article="12345678", product_name="Товар"),
    ])
    await session.commit()
    st = (await session.execute(select(SystemSetting))).scalar_one()
    assert st.is_editable is True and st.is_secret is False
    art = (await session.execute(select(Article))).scalar_one()
    assert art.is_active is True


def test_status_enums():
    assert {s.value for s in TaskStatus} == {
        "created", "in_progress", "completed", "waiting_approval", "approved",
        "auto_approved", "postponed", "problem", "overdue", "cancelled",
    }
    assert {s.value for s in CheckStatus} == {
        "pending", "checked_no_action", "action_required", "question",
    }
