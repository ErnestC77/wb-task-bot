from datetime import datetime, time, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import func, select

from bot.database.models import AdminAuditLog, Article, TaskConfig, Topic, User
from bot.database.repositories.audit_repository import AuditRepository
from bot.services.google_sheets_service import (
    DELIVERY_LOG_HEADER, GoogleSheetsService, SheetsClient, auto_sync_job,
    delivery_log_job, status_history_job,
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
     "time": "18:00", "due_time": "18:00", "due_days_offset": "", "active": "1"},
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


async def test_auto_sync_job_rebuilds_jobs_for_changed_configs(session_factory):
    """Часть А: фоновый auto_sync_job после коммита пересобирает джобы
    изменённых конфигов через переданный SchedulerService."""
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from bot.services.scheduler_service import SchedulerService

    async with session_factory() as s:
        await SettingService(s).set("sync.auto_enabled", True, actor_user_id=None)
        await s.commit()

    scheduler = AsyncIOScheduler()
    svc = SchedulerService(scheduler, AsyncMock(), session_factory)
    task_rows = [{"external_task_id": "auto_resync_me", "title": "Задача",
                  "scenario": "simple", "schedule_type": "daily",
                  "time": "18:00", "due_time": "18:00", "active": "1"}]
    fake_client = MagicMock()
    fake_client.read_rows = lambda sheet_name: (
        task_rows if sheet_name == "Tasks_Config" else [])
    fake_settings = MagicMock(google_sheets_credentials_file="creds.json",
                              google_sheets_spreadsheet_id="sheet-id")

    with patch("bot.services.google_sheets_service.SheetsClient",
               return_value=fake_client), \
         patch("bot.services.google_sheets_service.get_settings",
               return_value=fake_settings):
        await auto_sync_job(AsyncMock(), session_factory, scheduler_svc=svc)

    async with session_factory() as s:
        cfg = await s.scalar(select(TaskConfig).where(
            TaskConfig.external_task_id == "auto_resync_me"))
        assert cfg is not None
        assert cfg.next_run_at is not None
        cfg_id = cfg.id
    assert scheduler.get_job(f"config:{cfg_id}") is not None


async def test_register_sync_job_passes_scheduler_service_to_job(session_factory):
    """register_sync_job обязан отдавать job'у сам SchedulerService третьим
    аргументом — иначе auto_sync_job не сможет пересобрать джобы конфигов."""
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from bot.services.scheduler_service import SchedulerService

    async with session_factory() as s:
        await SettingService(s).set("sync.auto_enabled", True, actor_user_id=None)
        await s.commit()

    scheduler = AsyncIOScheduler()
    svc = SchedulerService(scheduler, AsyncMock(), session_factory)
    await svc.register_sync_job()
    job = scheduler.get_job("auto_sync")
    assert job is not None
    assert len(job.args) == 3
    assert job.args[2] is svc


def _client_with_fake_spreadsheet(fake) -> SheetsClient:
    """SheetsClient без __init__ (реальный конструктор ходит в Google API);
    тесты подставляют фейковый gspread-Spreadsheet напрямую — тот же принцип
    «тонкая обёртка подменяется фейком», что и для read_rows."""
    client = SheetsClient.__new__(SheetsClient)
    client._spreadsheet = fake
    return client


def test_scopes_allow_write():
    from bot.services.google_sheets_service import SCOPES
    assert SCOPES == ["https://www.googleapis.com/auth/spreadsheets"]


def test_append_rows_appends_to_existing_sheet():
    ws = MagicMock()
    fake = MagicMock()
    fake.worksheet.return_value = ws
    client = _client_with_fake_spreadsheet(fake)
    client.append_rows("Журнал отправок", [["Задача 1", "Товары",
                                            "10.07.2026 09:01", "10.07.2026 12:00"]],
                       DELIVERY_LOG_HEADER)
    ws.append_rows.assert_called_once_with(
        [["Задача 1", "Товары", "10.07.2026 09:01", "10.07.2026 12:00"]])
    fake.add_worksheet.assert_not_called()


def test_append_rows_creates_missing_sheet_with_header():
    import gspread
    ws = MagicMock()
    fake = MagicMock()
    fake.worksheet.side_effect = gspread.exceptions.WorksheetNotFound("нет листа")
    fake.add_worksheet.return_value = ws
    client = _client_with_fake_spreadsheet(fake)
    client.append_rows("Журнал отправок", [["a", "b", "c", "d"]], DELIVERY_LOG_HEADER)
    ws.append_row.assert_called_once_with(
        ["Задача", "Чат/тема", "Время отправки", "Дедлайн"])
    ws.append_rows.assert_called_once_with([["a", "b", "c", "d"]])


def test_append_rows_uses_passed_header_for_new_sheet():
    """Часть Д: заголовок автосоздаваемого листа — параметр append_rows, а не
    константа «Журнала отправок»: у «Истории статусов» другие колонки."""
    import gspread
    ws = MagicMock()
    fake = MagicMock()
    fake.worksheet.side_effect = gspread.exceptions.WorksheetNotFound("нет листа")
    fake.add_worksheet.return_value = ws
    client = _client_with_fake_spreadsheet(fake)
    header = ["Задача", "Был статус", "Стал статус", "Кто", "Когда"]
    client.append_rows("История статусов", [["a", "b", "c", "d", "e"]], header)
    fake.add_worksheet.assert_called_once_with(title="История статусов", rows=1, cols=5)
    ws.append_row.assert_called_once_with(header)
    ws.append_rows.assert_called_once_with([["a", "b", "c", "d", "e"]])


# ---------------------------------------------------------------------------
# Часть Б: delivery_log_job — выгрузка отправленных задач в «Журнал отправок»
# ---------------------------------------------------------------------------

async def _make_delivery_instances(session_factory) -> dict[str, int]:
    """Три инстанса одного конфига: SENT (должен попасть в журнал), PENDING и
    FAILED (не должны). Возвращает {'sent': id, 'pending': id, 'failed': id}."""
    from bot.database.models import DeliveryStatus, Role
    from bot.database.repositories.task_repository import TaskRepository
    from bot.database.repositories.topic_repository import TopicRepository
    from bot.database.repositories.user_repository import UserRepository
    from bot.services.task_service import TaskService

    async with session_factory() as s:
        topic = await TopicRepository(s).upsert(topic_key="goods", topic_name="Товары",
                                                message_thread_id=10)
        user = await UserRepository(s).upsert(telegram_id=10, name="Валя",
                                              role=Role.MANAGER_WB)
        cfg = await TaskRepository(s).upsert_config(dict(
            external_task_id="log_me", title="Проверка артикулов", scenario="simple",
            schedule_type="daily", topic_id=topic.id, due_time=time(12, 0),
            responsible_user_id=user.id, is_active=True))
        svc = TaskService(s)
        sent = await svc.create_instance_for(cfg, datetime(2026, 7, 10, 9, 0))
        pending = await svc.create_instance_for(cfg, datetime(2026, 7, 11, 9, 0))
        failed = await svc.create_instance_for(cfg, datetime(2026, 7, 12, 9, 0))
        sent.delivery_status = DeliveryStatus.SENT
        sent.message_sent_at = datetime(2026, 7, 10, 9, 1)
        failed.delivery_status = DeliveryStatus.FAILED
        await s.commit()
        return {"sent": sent.id, "pending": pending.id, "failed": failed.id}


def _delivery_log_env():
    fake_settings = MagicMock(google_sheets_credentials_file="creds.json",
                              google_sheets_spreadsheet_id="sheet-id")
    fake_client = MagicMock()
    return fake_settings, fake_client


async def _enable_delivery_log(session_factory) -> None:
    async with session_factory() as s:
        await SettingService(s).set("delivery_log.enabled", True, actor_user_id=None)
        await s.commit()


async def test_delivery_log_job_skips_when_disabled(session_factory):
    from bot.database.models import TaskInstance

    ids = await _make_delivery_instances(session_factory)
    fake_settings, fake_client = _delivery_log_env()
    with patch("bot.services.google_sheets_service.SheetsClient",
               return_value=fake_client) as client_cls, \
         patch("bot.services.google_sheets_service.get_settings",
               return_value=fake_settings):
        await delivery_log_job(AsyncMock(), session_factory)   # enabled=False по умолчанию
    client_cls.assert_not_called()
    async with session_factory() as s:
        inst = await s.get(TaskInstance, ids["sent"])
        assert inst.sheet_logged_at is None


async def test_delivery_log_job_logs_only_sent_once(session_factory):
    """Только SENT попадает в журнал, ровно один раз (идемпотентность через
    sheet_logged_at), одним пакетным append_rows с корректной строкой."""
    from bot.database.models import TaskInstance

    ids = await _make_delivery_instances(session_factory)
    await _enable_delivery_log(session_factory)
    fake_settings, fake_client = _delivery_log_env()
    with patch("bot.services.google_sheets_service.SheetsClient",
               return_value=fake_client), \
         patch("bot.services.google_sheets_service.get_settings",
               return_value=fake_settings):
        await delivery_log_job(AsyncMock(), session_factory)
        await delivery_log_job(AsyncMock(), session_factory)   # повторный прогон

    fake_client.append_rows.assert_called_once()               # дублей нет
    sheet_name, rows, header = fake_client.append_rows.call_args.args
    assert sheet_name == "Журнал отправок"
    assert header == DELIVERY_LOG_HEADER
    assert rows == [["Проверка артикулов", "Товары",
                     "10.07.2026 09:01", "10.07.2026 12:00"]]
    async with session_factory() as s:
        assert (await s.get(TaskInstance, ids["sent"])).sheet_logged_at is not None
        assert (await s.get(TaskInstance, ids["pending"])).sheet_logged_at is None
        assert (await s.get(TaskInstance, ids["failed"])).sheet_logged_at is None


async def test_delivery_log_job_error_keeps_rows_for_retry(session_factory):
    """Недоступность Sheets: исключение ловится, ни одна строка не помечена —
    на следующем интервале весь пакет уходит повторно."""
    from bot.database.models import TaskInstance

    ids = await _make_delivery_instances(session_factory)
    await _enable_delivery_log(session_factory)
    fake_settings, fake_client = _delivery_log_env()
    fake_client.append_rows.side_effect = RuntimeError("quota exceeded")
    with patch("bot.services.google_sheets_service.SheetsClient",
               return_value=fake_client), \
         patch("bot.services.google_sheets_service.get_settings",
               return_value=fake_settings):
        await delivery_log_job(AsyncMock(), session_factory)   # не должен упасть наружу
        async with session_factory() as s:
            assert (await s.get(TaskInstance, ids["sent"])).sheet_logged_at is None
        fake_client.append_rows.side_effect = None             # Sheets «ожил»
        await delivery_log_job(AsyncMock(), session_factory)
    assert fake_client.append_rows.call_count == 2             # ретрай состоялся
    async with session_factory() as s:
        assert (await s.get(TaskInstance, ids["sent"])).sheet_logged_at is not None


async def test_sync_tasks_time_and_due_time_independent(session_factory):
    """Часть В: time (отправка) и due_time (дедлайн) — независимые колонки
    листа; due_days_offset читается из своей колонки."""
    row = [{"external_task_id": "independent", "title": "Задача", "scenario": "simple",
            "schedule_type": "daily", "time": "10:00", "due_time": "18:00",
            "due_days_offset": "2", "active": "1"}]
    async with session_factory() as s:
        svc = GoogleSheetsService(s, client=None)
        await svc.sync_tasks(row, dry_run=False)
        await s.commit()
        cfg = await s.scalar(select(TaskConfig).where(
            TaskConfig.external_task_id == "independent"))
        assert cfg.time.isoformat() == "07:00:00"       # 10:00 МСК -> 07:00 UTC
        assert cfg.due_time.isoformat() == "18:00:00"   # дедлайн — как в таблице, МСК
        assert cfg.due_days_offset == 2


async def test_sync_tasks_missing_new_columns_default(session_factory):
    """Обратная совместимость: без колонок time/due_days_offset — time=None
    (планировщик подставит дефолт 09:00), offset=0 (дедлайн в день отправки)."""
    row = [{"external_task_id": "no_new_columns", "title": "Задача",
            "scenario": "simple", "schedule_type": "daily",
            "due_time": "18:00", "active": "1"}]
    async with session_factory() as s:
        svc = GoogleSheetsService(s, client=None)
        await svc.sync_tasks(row, dry_run=False)
        await s.commit()
        cfg = await s.scalar(select(TaskConfig).where(
            TaskConfig.external_task_id == "no_new_columns"))
        assert cfg.time is None                         # больше НЕ выводится из due_time
        assert cfg.due_time.isoformat() == "18:00:00"
        assert cfg.due_days_offset == 0


# ---------------------------------------------------------------------------
# Часть Д: status_history_job — выгрузка истории статусов в «История статусов»
# ---------------------------------------------------------------------------

async def _make_status_logs(session_factory) -> dict[str, int]:
    """Инстанс + четыре записи TaskLog: first (old_status IS NULL — первое
    создание), manual (переход от Вали), auto (user_id IS NULL — авто-переход)
    и logged (уже выгруженная — не должна попасть в лист повторно)."""
    from bot.database.models import Role, TaskLog
    from bot.database.repositories.task_repository import TaskRepository
    from bot.database.repositories.user_repository import UserRepository
    from bot.services.task_service import TaskService

    async with session_factory() as s:
        valya = await UserRepository(s).upsert(telegram_id=10, name="Валя",
                                               role=Role.MANAGER_WB)
        cfg = await TaskRepository(s).upsert_config(dict(
            external_task_id="history_me", title="Проверка артикулов",
            scenario="simple", schedule_type="daily",
            responsible_user_id=valya.id, is_active=True))
        inst = await TaskService(s).create_instance_for(cfg, datetime(2026, 7, 10, 9, 0))
        first = TaskLog(task_instance_id=inst.id, user_id=None, action="task.create",
                        old_status=None, new_status="created",
                        created_at=datetime(2026, 7, 10, 9, 0))
        manual = TaskLog(task_instance_id=inst.id, user_id=valya.id, action="task.take",
                         old_status="created", new_status="in_progress",
                         created_at=datetime(2026, 7, 10, 9, 5))
        auto = TaskLog(task_instance_id=inst.id, user_id=None, action="auto:overdue",
                       old_status="in_progress", new_status="overdue",
                       created_at=datetime(2026, 7, 11, 9, 0))
        logged = TaskLog(task_instance_id=inst.id, user_id=valya.id, action="task.done",
                         old_status="overdue", new_status="completed",
                         created_at=datetime(2026, 7, 11, 12, 0),
                         sheet_logged_at=datetime(2026, 7, 11, 13, 0))
        s.add_all([first, manual, auto, logged])
        await s.commit()
        return {"first": first.id, "manual": manual.id,
                "auto": auto.id, "logged": logged.id}


async def _enable_status_history(session_factory) -> None:
    async with session_factory() as s:
        await SettingService(s).set("status_history_log.enabled", True,
                                    actor_user_id=None)
        await s.commit()


async def test_status_history_job_skips_when_disabled(session_factory):
    from bot.database.models import TaskLog

    ids = await _make_status_logs(session_factory)
    fake_settings, fake_client = _delivery_log_env()
    with patch("bot.services.google_sheets_service.SheetsClient",
               return_value=fake_client) as client_cls, \
         patch("bot.services.google_sheets_service.get_settings",
               return_value=fake_settings):
        await status_history_job(AsyncMock(), session_factory)  # enabled=False по умолчанию
    client_cls.assert_not_called()
    async with session_factory() as s:
        assert (await s.get(TaskLog, ids["manual"])).sheet_logged_at is None


async def test_status_history_job_logs_every_transition_once(session_factory):
    """КАЖДАЯ невыгруженная запись TaskLog попадает в лист ровно один раз:
    без фильтра по статусу; old_status IS NULL -> «—»; для авто-переходов
    (user_id IS NULL) колонка «Кто» показывает action, а не падает на None."""
    from bot.database.models import TaskLog

    ids = await _make_status_logs(session_factory)
    await _enable_status_history(session_factory)
    fake_settings, fake_client = _delivery_log_env()
    with patch("bot.services.google_sheets_service.SheetsClient",
               return_value=fake_client), \
         patch("bot.services.google_sheets_service.get_settings",
               return_value=fake_settings):
        await status_history_job(AsyncMock(), session_factory)
        await status_history_job(AsyncMock(), session_factory)  # повторный прогон

    fake_client.append_rows.assert_called_once()                # дублей нет
    sheet_name, rows, header = fake_client.append_rows.call_args.args
    assert sheet_name == "История статусов"
    assert header == ["Задача", "Был статус", "Стал статус", "Кто", "Когда"]
    assert rows == [
        ["Проверка артикулов", "—", "created", "task.create", "10.07.2026 09:00"],
        ["Проверка артикулов", "created", "in_progress", "Валя", "10.07.2026 09:05"],
        ["Проверка артикулов", "in_progress", "overdue", "auto:overdue",
         "11.07.2026 09:00"],
    ]
    async with session_factory() as s:
        for key in ("first", "manual", "auto"):
            assert (await s.get(TaskLog, ids[key])).sheet_logged_at is not None
        # Часть Г не затронута: свой флаг owner_notified_at этот job не трогает
        assert (await s.get(TaskLog, ids["auto"])).owner_notified_at is None


async def test_status_history_job_error_keeps_rows_for_retry(session_factory):
    """Сбой Google API: исключение ловится, ничего не помечено — весь пакет
    уходит повторно на следующем интервале (та же обработка ошибок, что у
    delivery_log_job, Часть Б)."""
    from bot.database.models import TaskLog

    ids = await _make_status_logs(session_factory)
    await _enable_status_history(session_factory)
    fake_settings, fake_client = _delivery_log_env()
    fake_client.append_rows.side_effect = RuntimeError("quota exceeded")
    with patch("bot.services.google_sheets_service.SheetsClient",
               return_value=fake_client), \
         patch("bot.services.google_sheets_service.get_settings",
               return_value=fake_settings):
        await status_history_job(AsyncMock(), session_factory)  # не должен упасть наружу
        async with session_factory() as s:
            assert (await s.get(TaskLog, ids["manual"])).sheet_logged_at is None
        fake_client.append_rows.side_effect = None              # Sheets «ожил»
        await status_history_job(AsyncMock(), session_factory)
    assert fake_client.append_rows.call_count == 2              # ретрай состоялся
    async with session_factory() as s:
        assert (await s.get(TaskLog, ids["manual"])).sheet_logged_at is not None
