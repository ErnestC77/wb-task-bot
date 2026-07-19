"""Audit log для webadmin (инцидент 2026-07-19: между 07-17 и 07-19 кто-то
правил задачи через webadmin без единой записи в admin_audit_log - webadmin
не логировал вообще ничего, в отличие от Telegram /admin).

actor_user_id везде None (осознанно, см. webadmin/audit.py) - webadmin
аутентифицирует по ОДНОМУ общему паролю на весь staff, различить сотрудников
невозможно без отдельной системы идентификации. Тесты вызывают функции
роутеров НАПРЯМУЮ (не через httpx client) - на happy path `request` не
используется (только в error-ветках рендера формы), поэтому можно передать
None и не зависеть от сломанной Jinja2-фикстуры (см. tests/webadmin/
test_tasks_crud.py - те же 4 теста падают и без моих изменений).
"""
from datetime import date, time
from unittest.mock import AsyncMock

from sqlalchemy import select

from bot.database.models import AdminAuditLog, Role
from bot.database.repositories.task_repository import TaskRepository
from bot.database.repositories.user_repository import UserRepository
from webadmin.audit import _jsonify, log_create, log_edit, snapshot


def test_jsonify_converts_date_time_datetime_to_isoformat():
    assert _jsonify(time(9, 30)) == "09:30:00"
    assert _jsonify(date(2026, 7, 19)) == "2026-07-19"
    assert _jsonify(42) == 42
    assert _jsonify(None) is None
    assert _jsonify("x") == "x"


async def test_snapshot_reads_fields_and_jsonifies(session):
    cfg = await TaskRepository(session).upsert_config(dict(
        external_task_id="t", title="Т", schedule_type="daily",
        time=time(6, 0), is_active=True))
    await session.commit()
    snap = snapshot(cfg, ["title", "time", "is_active"])
    assert snap == {"title": "Т", "time": "06:00:00", "is_active": True}


async def _last_log(session) -> AdminAuditLog:
    return (await session.scalars(
        select(AdminAuditLog).order_by(AdminAuditLog.id.desc()))).first()


async def test_log_create_writes_row_with_no_actor_and_webadmin_source(session):
    await log_create(session, "task_config", 5, {"title": "Новая", "time": time(9, 0)})
    await session.commit()
    row = await _last_log(session)
    assert row.action == "task_config.create"
    assert row.actor_user_id is None
    assert row.ip_or_source == "webadmin"
    assert row.entity_type == "task_config" and row.entity_id == "5"
    assert '"09:00:00"' in row.new_value_json


async def test_log_edit_writes_old_and_new(session):
    await log_edit(session, "user", 3, {"name": "Старое"}, {"name": "Новое"})
    await session.commit()
    row = await _last_log(session)
    assert row.action == "user.edit"
    assert row.old_value_json == '{"name": "Старое"}'
    assert row.new_value_json == '{"name": "Новое"}'


async def test_task_create_route_logs_audit(session):
    from webadmin.routers.tasks import task_create

    await task_create(
        request=None, session=session, title="Аудит-тест", description="",
        scenario="simple", responsible_user_id="", responsible_role="", topic_id="",
        schedule_type="daily", schedule_value="", schedule_interval="", time="09:00",
        due_time="", due_days_offset="0", need_approval=None, is_active="on")
    row = await _last_log(session)
    assert row.action == "task_config.create"
    assert row.ip_or_source == "webadmin"


async def test_task_update_route_logs_old_and_new(session):
    from sqlalchemy import select as sa_select

    from bot.database.models import TaskConfig
    from webadmin.routers.tasks import task_create, task_update

    await task_create(
        request=None, session=session, title="До правки", description="",
        scenario="simple", responsible_user_id="", responsible_role="", topic_id="",
        schedule_type="daily", schedule_value="", schedule_interval="", time="09:00",
        due_time="", due_days_offset="0", need_approval=None, is_active=None)
    cfg = await session.scalar(sa_select(TaskConfig).where(TaskConfig.title == "До правки"))
    await task_update(
        cfg.id, request=None, session=session, title="После правки", description="",
        scenario="simple", responsible_user_id="", responsible_role="", topic_id="",
        schedule_type="daily", schedule_value="", schedule_interval="", time="10:00",
        due_time="", due_days_offset="0", need_approval=None, is_active="on")
    row = await _last_log(session)
    assert row.action == "task_config.edit"
    assert '"До правки"' in row.old_value_json
    assert '"После правки"' in row.new_value_json


async def test_user_create_and_update_route_logs_audit(session):
    from webadmin.routers.users import user_create, user_update

    await user_create(
        request=None, session=session, telegram_id="555", name="Первый",
        role=Role.MANAGER_WB, is_active="on", private_chat_available=None)
    user = await UserRepository(session).get_by_telegram_id(555)
    row = await _last_log(session)
    assert row.action == "user.create"

    await user_update(
        user.id, request=None, session=session, name="Второй", role=Role.MANAGER_WB,
        is_active="on", private_chat_available=None)
    row = await _last_log(session)
    assert row.action == "user.edit"
    assert '"Первый"' in row.old_value_json
    assert '"Второй"' in row.new_value_json


async def test_topic_create_and_update_route_logs_audit(session):
    from webadmin.routers.topics import topic_create, topic_update

    await topic_create(
        request=None, session=session, topic_key="new_topic", topic_name="Новая тема",
        message_thread_id="", event_types="", is_active="on")
    from bot.database.repositories.topic_repository import TopicRepository
    topic = await TopicRepository(session).get_by_key("new_topic")
    row = await _last_log(session)
    assert row.action == "topic.create"

    await topic_update(
        topic.id, request=None, session=session, topic_name="Другая тема",
        message_thread_id="7", event_types="", is_active="on")
    row = await _last_log(session)
    assert row.action == "topic.edit"


async def test_article_create_and_update_route_logs_audit(session):
    from webadmin.routers.articles import article_create, article_update

    await article_create(
        request=None, session=session, article="10000099", product_name="Товар",
        sort_order="0", is_active="on")
    from bot.database.repositories.article_repository import ArticleRepository
    row_article = await ArticleRepository(session).get_by_article("10000099")
    row = await _last_log(session)
    assert row.action == "article.create"

    await article_update(
        row_article.id, request=None, session=session, product_name="Товар 2",
        sort_order="1", is_active="on")
    row = await _last_log(session)
    assert row.action == "article.edit"
