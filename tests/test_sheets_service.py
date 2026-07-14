from datetime import datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import func, select

from bot.database.models import AdminAuditLog, Article, TaskConfig, Topic, User
from bot.database.repositories.audit_repository import AuditRepository
from bot.services.google_sheets_service import (
    GoogleSheetsService, SheetsClient, auto_sync_job,
)
from bot.services.setting_service import SettingService


ARTICLE_ROWS = [
    {"article": "11111111", "product_name": "Товар 1", "sort_order": "1", "active": "1"},
    {"article": "22222222", "product_name": "Товар 2", "sort_order": "2", "active": "1"},
]


async def test_sync_articles_adds_and_deactivates(session_factory):
    async with session_factory() as s:
        svc = GoogleSheetsService(s, client=None)
        report = await svc.sync_articles(ARTICLE_ROWS, dry_run=False)
        await s.commit()
        assert report.added == 2
    # второй запуск без одного артикула — деактивация, не удаление
    async with session_factory() as s:
        svc = GoogleSheetsService(s, client=None)
        report = await svc.sync_articles(ARTICLE_ROWS[:1], dry_run=False)
        await s.commit()
        assert report.deactivated == 1
        rows = list(await s.scalars(select(Article)))
        assert len(rows) == 2                          # обе записи на месте
        assert [a.is_active for a in sorted(rows, key=lambda a: a.article)] == [True, False]


async def test_dry_run_changes_nothing(session_factory):      # тест 19 (админ)
    async with session_factory() as s:
        svc = GoogleSheetsService(s, client=None)
        report = await svc.sync_articles(ARTICLE_ROWS, dry_run=True)
        assert report.added == 2                       # preview показывает изменения
    async with session_factory() as s:
        n = await s.scalar(select(func.count(Article.id)))
        assert n == 0                                  # но в БД ничего нет


USER_ROWS = [
    {"telegram_id": "1", "name": "Валя", "role": "manager_wb", "active": "1"},
    {"telegram_id": "2", "name": "Пётр", "role": "logistic", "active": "1"},
]


async def test_sync_users_adds_and_deactivates(session_factory):
    async with session_factory() as s:
        svc = GoogleSheetsService(s, client=None)
        report = await svc.sync_users(USER_ROWS, dry_run=False)
        await s.commit()
        assert report.added == 2
    async with session_factory() as s:
        svc = GoogleSheetsService(s, client=None)
        report = await svc.sync_users(USER_ROWS[:1], dry_run=False)
        await s.commit()
        assert report.deactivated == 1
        rows = list(await s.scalars(select(User)))
        assert len(rows) == 2
        assert [u.is_active for u in sorted(rows, key=lambda u: u.telegram_id)] == [True, False]


async def test_sync_users_conflict_policy_admin_wins_skips_recent_edit(session_factory):
    async with session_factory() as s:
        # первая синхронизация — создаёт пользователя и фиксирует sync.run в аудите
        svc = GoogleSheetsService(s, client=None)
        await svc.sync_users(USER_ROWS[:1], dry_run=False)
        entry = await AuditRepository(s).add(actor_user_id=None, action="sync.run",
                                             new_value_json="{}", result="ok")
        # SQLite CURRENT_TIMESTAMP имеет секундную точность — чтобы сравнение
        # updated_at > last_sync_at было детерминированным (не завязанным на
        # реальные миллисекунды между операциями), «последний sync» явно
        # отодвигаем на час назад.
        entry.created_at = datetime.utcnow() - timedelta(hours=1)
        await s.commit()

    async with session_factory() as s:
        # админ правит пользователя ПОСЛЕ последней синхронизации
        user = await s.scalar(select(User).where(User.telegram_id == 1))
        user.name = "Валентина (правка админа)"
        await s.commit()

    async with session_factory() as s:
        settings = SettingService(s)
        assert await settings.get("sync.conflict_policy") == "admin_wins"   # policy по умолчанию
        svc = GoogleSheetsService(s, client=None)
        edited_row = [{"telegram_id": "1", "name": "Из таблицы", "role": "manager_wb", "active": "1"}]
        report = await svc.sync_users(edited_row, dry_run=False)
        await s.commit()
        assert report.updated == 0
        assert report.skipped_conflicts == ["user:1"]

    async with session_factory() as s:
        user = await s.scalar(select(User).where(User.telegram_id == 1))
        assert user.name == "Валентина (правка админа)"       # правка админа не перезаписана


async def test_sync_users_conflict_policy_sheets_wins_overwrites(session_factory):
    async with session_factory() as s:
        svc = GoogleSheetsService(s, client=None)
        await svc.sync_users(USER_ROWS[:1], dry_run=False)
        entry = await AuditRepository(s).add(actor_user_id=None, action="sync.run",
                                             new_value_json="{}", result="ok")
        entry.created_at = datetime.utcnow() - timedelta(hours=1)   # см. комментарий выше
        await s.commit()

    async with session_factory() as s:
        # тот же сценарий конфликта (правка ПОСЛЕ последнего sync), но с
        # другой политикой — она должна перезаписать, а не пропустить
        user = await s.scalar(select(User).where(User.telegram_id == 1))
        user.name = "Валентина (правка админа)"
        await s.commit()

    async with session_factory() as s:
        settings = SettingService(s)
        await settings.set("sync.conflict_policy", "sheets_wins", actor_user_id=None)
        svc = GoogleSheetsService(s, client=None)
        edited_row = [{"telegram_id": "1", "name": "Из таблицы", "role": "manager_wb", "active": "1"}]
        report = await svc.sync_users(edited_row, dry_run=False)
        await s.commit()
        assert report.updated == 1
        assert report.skipped_conflicts == []

    async with session_factory() as s:
        user = await s.scalar(select(User).where(User.telegram_id == 1))
        assert user.name == "Из таблицы"


TOPIC_ROWS = [
    {"topic_key": "goods", "topic_name": "Товары", "message_thread_id": "10", "active": "1"},
]


async def test_sync_topics_add_update_deactivate(session_factory):
    async with session_factory() as s:
        svc = GoogleSheetsService(s, client=None)
        report = await svc.sync_topics(TOPIC_ROWS, dry_run=False)
        await s.commit()
        assert report.added == 1
    async with session_factory() as s:
        svc = GoogleSheetsService(s, client=None)
        renamed = [{"topic_key": "goods", "topic_name": "Товары (новое имя)",
                   "message_thread_id": "10", "active": "1"}]
        report = await svc.sync_topics(renamed, dry_run=False)
        await s.commit()
        assert report.updated == 1
    async with session_factory() as s:
        svc = GoogleSheetsService(s, client=None)
        report = await svc.sync_topics([], dry_run=False)
        await s.commit()
        assert report.deactivated == 1
        topic = await s.scalar(select(Topic).where(Topic.topic_key == "goods"))
        assert topic is not None                       # не удалён
        assert topic.is_active is False
        assert topic.topic_name == "Товары (новое имя)"


TASK_ROWS = [
    {"external_task_id": "articles_check_all", "title": "Проверка артикулов",
     "scenario": "article_check", "schedule_type": "daily", "schedule_interval": "",
     "due_time": "18:00", "active": "1"},
]


async def test_sync_tasks_add_and_deactivate(session_factory):
    async with session_factory() as s:
        svc = GoogleSheetsService(s, client=None)
        report = await svc.sync_tasks(TASK_ROWS, dry_run=False)
        await s.commit()
        assert report.added == 1
        cfg = await s.scalar(select(TaskConfig).where(
            TaskConfig.external_task_id == "articles_check_all"))
        assert cfg.scenario == "article_check"
        assert cfg.due_time.isoformat() == "18:00:00"     # дедлайн в тексте — как в таблице, МСК
        assert cfg.time.isoformat() == "15:00:00"         # реальный час отправки — в UTC (МСК-3)

    async with session_factory() as s:
        svc = GoogleSheetsService(s, client=None)
        report = await svc.sync_tasks([], dry_run=False)
        await s.commit()
        assert report.deactivated == 1
        cfg = await s.scalar(select(TaskConfig).where(
            TaskConfig.external_task_id == "articles_check_all"))
        assert cfg is not None                          # не удалена
        assert cfg.is_active is False


async def test_sync_tasks_weekly_accepts_day_name(session_factory):
    row = [{"external_task_id": "weekly_named_day", "title": "Отчёт",
           "scenario": "simple", "schedule_type": "weekly",
           "schedule_value": "Понедельник", "active": "1"}]
    async with session_factory() as s:
        svc = GoogleSheetsService(s, client=None)
        await svc.sync_tasks(row, dry_run=False)
        await s.commit()
        cfg = await s.scalar(select(TaskConfig).where(
            TaskConfig.external_task_id == "weekly_named_day"))
        assert cfg.schedule_value == "0"

    row2 = [{**row[0], "schedule_value": "пт"}]
    async with session_factory() as s:
        svc = GoogleSheetsService(s, client=None)
        await svc.sync_tasks(row2, dry_run=False)
        await s.commit()
        cfg = await s.scalar(select(TaskConfig).where(
            TaskConfig.external_task_id == "weekly_named_day"))
        assert cfg.schedule_value == "4"

    row3 = [{**row[0], "schedule_value": "3"}]
    async with session_factory() as s:
        svc = GoogleSheetsService(s, client=None)
        await svc.sync_tasks(row3, dry_run=False)
        await s.commit()
        cfg = await s.scalar(select(TaskConfig).where(
            TaskConfig.external_task_id == "weekly_named_day"))
        assert cfg.schedule_value == "3"                # число по-прежнему работает


def test_moscow_to_utc_conversion():
    from datetime import time as time_
    from bot.services.google_sheets_service import _moscow_to_utc
    assert _moscow_to_utc(time_(18, 0)) == time_(15, 0)
    assert _moscow_to_utc(time_(10, 0)) == time_(7, 0)
    assert _moscow_to_utc(time_(1, 0)) == time_(22, 0)    # переход через полночь
    assert _moscow_to_utc(None) is None


async def test_sync_tasks_weekly_accepts_multiple_days(session_factory):
    row = [{"external_task_id": "weekly_multi_day", "title": "Отчёт",
           "scenario": "simple", "schedule_type": "weekly",
           "schedule_value": "понедельник, ср, 4", "active": "1"}]
    async with session_factory() as s:
        svc = GoogleSheetsService(s, client=None)
        await svc.sync_tasks(row, dry_run=False)
        await s.commit()
        cfg = await s.scalar(select(TaskConfig).where(
            TaskConfig.external_task_id == "weekly_multi_day"))
        assert cfg.schedule_value == "0,2,4"


async def test_sync_all_dry_run_writes_no_business_data_but_logs_audit(session_factory):
    client = AsyncMock(spec=SheetsClient)
    client.read_rows = lambda sheet_name: (
        ARTICLE_ROWS if sheet_name == "Articles" else USER_ROWS if sheet_name == "Users"
        else TOPIC_ROWS if sheet_name == "Topics" else TASK_ROWS)

    async with session_factory() as s:
        svc = GoogleSheetsService(s, client=client)
        results = await svc.sync_all(dry_run=True, actor_user_id=None)
        await s.commit()
        assert results["articles"].added == 2
        assert results["users"].added == 2

    async with session_factory() as s:
        assert await s.scalar(select(func.count(Article.id))) == 0
        assert await s.scalar(select(func.count(User.id))) == 0
        assert await s.scalar(select(func.count(Topic.id))) == 0
        assert await s.scalar(select(func.count(TaskConfig.id))) == 0
        # но факт запуска (dry-run preview) зафиксирован в аудите
        log = await s.scalar(select(AdminAuditLog).where(AdminAuditLog.action == "sync.run"))
        assert log is not None
        assert log.result == "dry_run"


async def test_sync_all_real_run_persists_and_logs_ok(session_factory):
    client = AsyncMock(spec=SheetsClient)
    client.read_rows = lambda sheet_name: (
        ARTICLE_ROWS if sheet_name == "Articles" else USER_ROWS if sheet_name == "Users"
        else TOPIC_ROWS if sheet_name == "Topics" else TASK_ROWS)

    async with session_factory() as s:
        svc = GoogleSheetsService(s, client=client)
        await svc.sync_all(dry_run=False, actor_user_id=7)
        await s.commit()

    async with session_factory() as s:
        assert await s.scalar(select(func.count(Article.id))) == 2
        assert await s.scalar(select(func.count(User.id))) == 2
        log = await s.scalar(select(AdminAuditLog).where(AdminAuditLog.action == "sync.run"))
        assert log is not None
        assert log.result == "ok"
        assert log.actor_user_id == 7


async def test_sync_all_critical_error_rolls_back_whole_run(session_factory):
    """Ошибка на ОДНОМ листе (Tasks_Config — битый due_time) не должна
    оставить в БД частично применённые изменения других листов (Users),
    обработанных раньше в том же sync_all: вся синхронизация — одна
    транзакция, коммитит её вызывающий код, а он не должен коммитить при
    исключении."""
    bad_task_rows = [{"external_task_id": "bad", "title": "x", "due_time": "not-a-time"}]
    client = AsyncMock(spec=SheetsClient)
    client.read_rows = lambda sheet_name: (
        USER_ROWS if sheet_name == "Users" else bad_task_rows if sheet_name == "Tasks_Config"
        else [])

    async with session_factory() as s:
        svc = GoogleSheetsService(s, client=client)
        with pytest.raises(ValueError):
            await svc.sync_all(dry_run=False, actor_user_id=None)
        # намеренно НЕ коммитим — как и должен вести себя вызывающий код
        # при исключении (см. auto_sync_job, который коммитит только после
        # успешного sync_all)

    async with session_factory() as s:
        assert await s.scalar(select(func.count(User.id))) == 0     # весь запуск откачен
        assert await s.scalar(select(func.count(TaskConfig.id))) == 0


async def test_auto_sync_job_skips_when_disabled(session_factory):
    bot = AsyncMock()
    await auto_sync_job(bot, session_factory)     # sync.auto_enabled=False по умолчанию
    async with session_factory() as s:
        assert await s.scalar(select(func.count(Article.id))) == 0
        log = await s.scalar(select(AdminAuditLog).where(AdminAuditLog.action == "sync.run"))
        assert log is None


async def test_sync_tasks_reports_changed_config_ids(session_factory):
    """Часть А: SyncReport перечисляет id добавленных/обновлённых/
    деактивированных конфигов — по ним вызывающий код пересоберёт джобы."""
    from bot.services.google_sheets_service import SyncReport

    assert SyncReport().changed_config_ids == []          # default — пустой список

    async with session_factory() as s:                    # добавление
        svc = GoogleSheetsService(s, client=None)
        report = await svc.sync_tasks(TASK_ROWS, dry_run=False)
        await s.commit()
        cfg = await s.scalar(select(TaskConfig).where(
            TaskConfig.external_task_id == "articles_check_all"))
        assert report.changed_config_ids == [cfg.id]
        cfg_id = cfg.id

    async with session_factory() as s:                    # обновление
        svc = GoogleSheetsService(s, client=None)
        report = await svc.sync_tasks(TASK_ROWS, dry_run=False)
        await s.commit()
        assert report.changed_config_ids == [cfg_id]

    async with session_factory() as s:                    # деактивация
        svc = GoogleSheetsService(s, client=None)
        report = await svc.sync_tasks([], dry_run=False)
        await s.commit()
        assert report.changed_config_ids == [cfg_id]
