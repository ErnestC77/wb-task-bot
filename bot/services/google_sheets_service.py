"""GoogleSheetsService (Task 22): импорт Users/Topics/Tasks_Config/Articles
из Google Sheets с dry-run, preview и conflict policy.

Механизм отката dry-run: каждый sync_* оборачивает свои изменения в
SAVEPOINT (``session.begin_nested()``) и при ``dry_run=True`` бросает
служебное исключение ``_DryRunRollback`` ВНУТРИ savepoint-блока —
SQLAlchemy откатывает именно этот savepoint (сохраняя уже наполненный
report), после чего исключение сразу гасится в самом sync_* — метод
возвращается наружу нормально, а не исключением. Это важно: тесты и
sync_all вызывают sync_articles()/sync_users()/... напрямую и ожидают
SyncReport, а не проброс исключения.

Conflict policy (sync.conflict_policy): "admin_wins" — если существующая
запись правилась через админ-панель ПОСЛЕ последней УСПЕШНОЙ (не dry-run)
синхронизации (updated_at > момент последней audit-записи
action="sync.run", result="ok"), запись НЕ перезаписывается данными из
таблицы и попадает в report.skipped_conflicts; "sheets_wins" —
перезаписывается всегда. Деактивация отсутствующих в Sheets записей
политикой НЕ управляется — действует всегда (Global Constraint плана:
физически ничего не удаляется).
"""
import json
from dataclasses import dataclass, field
from datetime import datetime, time

import gspread
from google.oauth2.service_account import Credentials
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import get_settings
from bot.database.models import ScheduleType, TaskScenario
from bot.database.repositories.audit_repository import AuditRepository
from bot.services.setting_service import SettingService
from bot.utils.logger import get_logger

logger = get_logger(__name__)
# Часть Б: полный scope вместо .readonly — «Журнал отправок» пишет в таблицу.
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
DELIVERY_LOG_HEADER = ["Задача", "Чат/тема", "Время отправки", "Дедлайн"]
# Часть А: поля, от которых зависит APScheduler-джоб конфига. rebuild_config_job
# пересчитывает next_run_at от "сейчас" (не идемпотентно для периодических
# расписаний) — вызывать его нужно только когда что-то из этого списка реально
# поменялось, иначе next_run_at будет сдвигаться вперёд при каждом автосинке.
_SCHEDULE_AFFECTING_FIELDS = (
    "time", "schedule_type", "schedule_value", "schedule_interval",
    "run_on_weekends", "is_active",
)


@dataclass
class SyncReport:
    added: int = 0
    updated: int = 0
    deactivated: int = 0
    skipped_conflicts: list[str] = field(default_factory=list)
    # id TaskConfig за проход sync_tasks(), у которых реально изменилось
    # хотя бы одно расписание-влияющее поле (_SCHEDULE_AFFECTING_FIELDS),
    # плюс все добавленные и все деактивированные (другие листы планировщик
    # не трогают — там поле пустое). Использовать ТОЛЬКО после реального
    # (dry_run=False) закоммиченного прогона: при dry-run savepoint
    # откатывается, id добавленных строк — временные и в БД не существуют.
    changed_config_ids: list[int] = field(default_factory=list)


class SheetsClient:
    """Тонкая обёртка gspread — единственное место, которое знает про
    реальный Google Sheets API. В тестах подменяется фейком/AsyncMock."""

    def __init__(self, credentials_file: str, spreadsheet_id: str) -> None:
        creds = Credentials.from_service_account_file(credentials_file, scopes=SCOPES)
        self._spreadsheet = gspread.authorize(creds).open_by_key(spreadsheet_id)

    def read_rows(self, sheet_name: str) -> list[dict]:
        return self._spreadsheet.worksheet(sheet_name).get_all_records()

    def append_rows(self, sheet_name: str, rows: list[list],
                    header: list[str]) -> None:
        """Дописывает строки в лист; если листа нет — создаёт его и пишет
        первой строкой переданный заголовок (Часть Б — «Журнал отправок»,
        Часть Д — «История статусов»)."""
        try:
            ws = self._spreadsheet.worksheet(sheet_name)
        except gspread.exceptions.WorksheetNotFound:
            ws = self._spreadsheet.add_worksheet(
                title=sheet_name, rows=1, cols=len(header))
            ws.append_row(header)
        ws.append_rows(rows)


class _DryRunRollback(Exception):
    """Служебное исключение: откатывает SAVEPOINT, сохраняя report."""

    def __init__(self, report: SyncReport) -> None:
        self.report = report


def _truthy(value: object, default: bool = True) -> bool:
    if value is None or value == "":
        return default
    return str(value).strip().lower() in ("1", "true", "yes", "да")


def _int_or_none(value: object) -> int | None:
    if value in (None, ""):
        return None
    return int(value)


def _str_or_none(value: object) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def _parse_time(value: object) -> time | None:
    if not value:
        return None
    if isinstance(value, time):
        return value
    text = str(value).strip()
    if not text:
        return None
    parts = text.split(":")
    return time(int(parts[0]), int(parts[1]) if len(parts) > 1 else 0)


MOSCOW_UTC_OFFSET_HOURS = 3


def _moscow_to_utc(t: time | None) -> time | None:
    """Tasks_Config.due_time вводится по московскому времени (МСК = UTC+3,
    без перехода на летнее/зимнее), а планировщик (scheduler_service.py)
    целиком работает в naive-UTC — TaskConfig.time (реальный час создания
    задачи) обязан быть в UTC, иначе задача уходит на 3 часа позже
    задуманного. Не обрабатывает переход через полночь (due_time 00:00-02:59
    МСК потребовал бы ещё и сдвига даты, которую здесь взять негде) — среди
    реальных значений в таблице такого пока нет."""
    if t is None:
        return None
    hour = (t.hour - MOSCOW_UTC_OFFSET_HOURS) % 24
    return t.replace(hour=hour)


_WEEKDAY_NAMES = {
    "понедельник": 0, "пн": 0,
    "вторник": 1, "вт": 1,
    "среда": 2, "ср": 2,
    "четверг": 3, "чт": 3,
    "пятница": 4, "пт": 4,
    "суббота": 5, "сб": 5,
    "воскресенье": 6, "вс": 6,
}


def _parse_schedule_value(schedule_type: str, value: object) -> str | None:
    """weekly принимает число (0=понедельник) и/или русское название дня
    ("понедельник"/"пн"), а также несколько дней через запятую —
    "понедельник, среда, пятница" или "пн,ср,пт" — сохраняются как "0,2,4"
    (scheduler_service.compute_next_run берёт ближайший из них). Для
    остальных schedule_type значение не трогаем (там либо число дня месяца,
    либо cron-выражение)."""
    text = _str_or_none(value)
    if text is None or schedule_type != ScheduleType.WEEKLY:
        return text
    parts = [p.strip() for p in text.split(",") if p.strip()]
    resolved = [str(_WEEKDAY_NAMES.get(p.lower(), p)) for p in parts]
    return ",".join(resolved)


class GoogleSheetsService:
    def __init__(self, session: AsyncSession, client: "SheetsClient | None") -> None:
        self.session = session
        self.client = client
        self.settings = SettingService(session)
        self.audit = AuditRepository(session)

    async def _conflict_policy(self) -> str:
        return str(await self.settings.get("sync.conflict_policy"))

    async def _last_sync_at(self) -> datetime | None:
        """Момент последней УСПЕШНОЙ (не dry-run) синхронизации — по
        последней audit-записи action="sync.run", result="ok". Dry-run
        запуски намеренно не считаются «последним sync» для целей
        конфликт-политики — они ничего не применяют."""
        entry = await self.audit.get_last_by_action("sync.run", result="ok")
        return entry.created_at if entry else None

    @staticmethod
    def _is_conflicted(existing: object, policy: str, last_sync_at: datetime | None) -> bool:
        return bool(
            existing is not None and policy == "admin_wins" and last_sync_at is not None
            and getattr(existing, "updated_at", None) is not None
            and existing.updated_at > last_sync_at)

    async def sync_articles(self, rows: list[dict], dry_run: bool) -> SyncReport:
        from bot.database.repositories.article_repository import ArticleRepository
        repo = ArticleRepository(self.session)
        report = SyncReport()
        seen: set[str] = set()
        try:
            async with self.session.begin_nested():
                for row in rows:
                    article = str(row.get("article", "")).strip()
                    if not article:
                        continue
                    seen.add(article)
                    existing = await repo.get_by_article(article)
                    await repo.upsert(
                        article=article,
                        product_name=_str_or_none(row.get("product_name")),
                        sort_order=_int_or_none(row.get("sort_order")) or 0,
                        is_active=_truthy(row.get("active", "1")),
                        source="google_sheets", source_updated_at=datetime.utcnow())
                    if existing is None:
                        report.added += 1
                    else:
                        report.updated += 1
                for stale in await repo.get_active_not_in(seen):
                    stale.is_active = False               # деактивация, не удаление
                    report.deactivated += 1
                await self.session.flush()
                if dry_run:
                    raise _DryRunRollback(report)
        except _DryRunRollback:
            pass
        return report

    async def sync_users(self, rows: list[dict], dry_run: bool) -> SyncReport:
        from bot.database.repositories.user_repository import UserRepository
        repo = UserRepository(self.session)
        report = SyncReport()
        policy = await self._conflict_policy()
        last_sync = await self._last_sync_at()
        seen: set[int] = set()
        try:
            async with self.session.begin_nested():
                for row in rows:
                    raw_id = row.get("telegram_id")
                    if raw_id in (None, ""):
                        continue
                    telegram_id = int(raw_id)
                    seen.add(telegram_id)
                    existing = await repo.get_by_telegram_id(telegram_id)
                    if self._is_conflicted(existing, policy, last_sync):
                        report.skipped_conflicts.append(f"user:{telegram_id}")
                        continue
                    await repo.upsert(
                        telegram_id=telegram_id,
                        name=str(row.get("name", "")),
                        role=str(row.get("role", "")),
                        username=_str_or_none(row.get("username")),
                        is_active=_truthy(row.get("active", "1")))
                    if existing is None:
                        report.added += 1
                    else:
                        report.updated += 1
                for stale in await repo.get_active_not_in(seen):
                    stale.is_active = False
                    report.deactivated += 1
                await self.session.flush()
                if dry_run:
                    raise _DryRunRollback(report)
        except _DryRunRollback:
            pass
        return report

    async def sync_topics(self, rows: list[dict], dry_run: bool) -> SyncReport:
        from bot.database.repositories.topic_repository import TopicRepository
        repo = TopicRepository(self.session)
        report = SyncReport()
        policy = await self._conflict_policy()
        last_sync = await self._last_sync_at()
        seen: set[str] = set()
        try:
            async with self.session.begin_nested():
                for row in rows:
                    topic_key = str(row.get("topic_key", "")).strip()
                    if not topic_key:
                        continue
                    seen.add(topic_key)
                    existing = await repo.get_by_key(topic_key)
                    if self._is_conflicted(existing, policy, last_sync):
                        report.skipped_conflicts.append(f"topic:{topic_key}")
                        continue
                    await repo.upsert(
                        topic_key=topic_key,
                        topic_name=str(row.get("topic_name", "")),
                        message_thread_id=_int_or_none(row.get("message_thread_id")),
                        event_types=_str_or_none(row.get("event_types")),
                        is_active=_truthy(row.get("active", "1")))
                    if existing is None:
                        report.added += 1
                    else:
                        report.updated += 1
                for stale in await repo.get_active_not_in(seen):
                    stale.is_active = False
                    report.deactivated += 1
                await self.session.flush()
                if dry_run:
                    raise _DryRunRollback(report)
        except _DryRunRollback:
            pass
        return report

    async def sync_tasks(self, rows: list[dict], dry_run: bool) -> SyncReport:
        from bot.database.repositories.task_repository import TaskRepository
        from bot.database.repositories.topic_repository import TopicRepository
        task_repo = TaskRepository(self.session)
        topic_repo = TopicRepository(self.session)
        report = SyncReport()
        policy = await self._conflict_policy()
        last_sync = await self._last_sync_at()
        seen: set[str] = set()
        try:
            async with self.session.begin_nested():
                for row in rows:
                    external_task_id = str(row.get("external_task_id", "")).strip()
                    if not external_task_id:
                        continue
                    seen.add(external_task_id)
                    existing = await task_repo.get_config_by_external_id(external_task_id)
                    if self._is_conflicted(existing, policy, last_sync):
                        report.skipped_conflicts.append(f"task:{external_task_id}")
                        continue
                    topic_key = _str_or_none(row.get("topic_key"))
                    topic = await topic_repo.get_by_key(topic_key) if topic_key else None
                    schedule_type = _str_or_none(row.get("schedule_type")) or ScheduleType.DAILY
                    data = {
                        "external_task_id": external_task_id,
                        "title": str(row.get("title", "")),
                        "description": _str_or_none(row.get("description")),
                        "scenario": _str_or_none(row.get("scenario")) or TaskScenario.SIMPLE,
                        "responsible_role": _str_or_none(row.get("responsible_role")),
                        "topic_id": topic.id if topic else None,
                        "schedule_type": schedule_type,
                        "schedule_value": _parse_schedule_value(
                            schedule_type, row.get("schedule_value")),
                        "schedule_interval": _int_or_none(row.get("schedule_interval")),
                        # time — момент, когда планировщик реально создаёт/отправляет
                        # задачу (compute_next_run, bot/services/scheduler_service.py,
                        # работает в naive-UTC). Часть В: читается из СОБСТВЕННОЙ
                        # колонки листа `time` (МСК -> UTC); раньше вычислялся из
                        # ячейки due_time — колонки были искусственно склеены.
                        # due_time (дедлайн, показывается сотрудникам текстом) —
                        # из своей колонки, без конвертации: по МСК, как в таблице.
                        # due_days_offset — дедлайн через N дней после дня отправки;
                        # пустая ячейка/нет колонки -> 0 (тот же день, как раньше).
                        "time": _moscow_to_utc(_parse_time(row.get("time"))),
                        "due_time": _parse_time(row.get("due_time")),
                        "due_days_offset": _int_or_none(row.get("due_days_offset")) or 0,
                        "run_on_weekends": _truthy(row.get("run_on_weekends", "1")),
                        "skip_holidays": _truthy(row.get("skip_holidays", "0"), default=False),
                        "need_approval": _truthy(row.get("need_approval", "0"), default=False),
                        "is_active": _truthy(row.get("active", "1")),
                    }
                    schedule_changed = existing is None or any(
                        getattr(existing, fname) != data[fname]
                        for fname in _SCHEDULE_AFFECTING_FIELDS)
                    cfg = await task_repo.upsert_config(data)
                    if schedule_changed:
                        report.changed_config_ids.append(cfg.id)
                    if existing is None:
                        report.added += 1
                    else:
                        report.updated += 1
                for stale in await task_repo.get_active_configs_not_in(seen):
                    stale.is_active = False
                    report.deactivated += 1
                    report.changed_config_ids.append(stale.id)
                await self.session.flush()
                if dry_run:
                    raise _DryRunRollback(report)
        except _DryRunRollback:
            pass
        return report

    async def sync_all(self, dry_run: bool, actor_user_id: int | None) -> dict[str, SyncReport]:
        """Единая транзакция по всем листам (users/topics/tasks/articles).

        dry_run=True: каждый sync_* откатывает свой SAVEPOINT — в бизнес-
        таблицы (users/topics/tasks_config/articles) не попадает НИ ОДНОГО
        изменения; отчёты (SyncReport) при этом всё равно наполняются, чтобы
        показать preview. Факт запуска (и то, что это был dry-run) всё же
        фиксируется в audit — это отдельная, не откатываемая запись.

        Критическая ошибка (исключение, отличное от _DryRunRollback) внутри
        любого sync_* НЕ перехватывается здесь — пробрасывается вызывающему
        коду. Вызывающий код (см. auto_sync_job) не должен коммитить сессию
        в этом случае — тем самым откатывается ВЕСЬ запуск целиком (Global
        Constraint: единая транзакция на весь sync_all).
        """
        names = {k: str(await self.settings.get(f"sync.sheet_{k}"))
                 for k in ("users", "topics", "tasks")}
        names["articles"] = str(await self.settings.get("article_check.sheet_name"))
        results: dict[str, SyncReport] = {}
        for kind, sheet in names.items():
            rows = self.client.read_rows(sheet) if self.client else []
            handler = getattr(self, f"sync_{kind}")
            results[kind] = await handler(rows, dry_run)
        await self.audit.add(
            actor_user_id=actor_user_id, action="sync.run",
            new_value_json=json.dumps({k: vars(v) for k, v in results.items()},
                                      ensure_ascii=False),
            result="dry_run" if dry_run else "ok")
        return results


async def auto_sync_job(bot, session_factory, scheduler_svc=None) -> None:
    """Job-обработчик автосинхронизации (регистрируется
    SchedulerService.register_sync_job — Task 12). Ничего не делает, если
    sync.auto_enabled=False.

    Часть А: после коммита пересобирает APScheduler-джобы изменённых
    конфигов через scheduler_svc (сам SchedulerService, передан
    register_sync_job'ом третьим аргументом). Пересборка — ПОСЛЕ выхода из
    сессии: rebuild_config_job открывает собственную сессию и должен видеть
    уже закоммиченные данные (правило Task 27/28/33)."""
    from bot.database.db import async_session_factory

    factory = session_factory or async_session_factory
    async with factory() as session:
        settings = SettingService(session)
        if not bool(await settings.get("sync.auto_enabled")):
            return
        spreadsheet_id = (str(await settings.get("sync.spreadsheet_id"))
                          or get_settings().google_sheets_spreadsheet_id)
        client = SheetsClient(get_settings().google_sheets_credentials_file, spreadsheet_id)
        results = await GoogleSheetsService(session, client).sync_all(
            dry_run=False, actor_user_id=None)
        await session.commit()
    if scheduler_svc is not None:
        for config_id in results["tasks"].changed_config_ids:
            await scheduler_svc.rebuild_config_job(config_id)


DELIVERY_LOG_SHEET_NAME = "Журнал отправок"
_DELIVERY_LOG_DT_FORMAT = "%d.%m.%Y %H:%M"


async def delivery_log_job(bot, session_factory) -> None:
    """Job-обработчик «Журнала отправок» (Часть Б; регистрируется
    SchedulerService.register_delivery_log_job). По строке на каждую
    фактически отправленную (delivery_status=SENT) TaskInstance, ещё не
    выгруженную (sheet_logged_at IS NULL): задача, чат/тема, время отправки,
    дедлайн (дата+время). Ничего не делает при delivery_log.enabled=False.

    Ошибка Google Sheets API (сеть/лимиты/credentials) логируется, ни одна
    строка НЕ помечается sheet_logged_at — весь пакет уйдёт повторно на
    следующем интервале. Отправка в Telegram уже случилась раньше и от
    успеха этого джоба не зависит."""
    from bot.database.db import async_session_factory
    from bot.database.repositories.task_repository import TaskRepository
    from bot.database.repositories.topic_repository import TopicRepository

    factory = session_factory or async_session_factory
    async with factory() as session:
        settings = SettingService(session)
        if not bool(await settings.get("delivery_log.enabled")):
            return
        task_repo = TaskRepository(session)
        topic_repo = TopicRepository(session)
        instances = await task_repo.get_sent_unlogged()
        if not instances:
            return
        rows: list[list] = []
        for inst in instances:
            topic_name = "—"
            if inst.topic_id is not None:
                topic = await topic_repo.get_by_id(inst.topic_id)
                topic_name = topic.topic_name if topic else "—"
            rows.append([
                inst.title_snapshot,
                topic_name,
                (inst.message_sent_at.strftime(_DELIVERY_LOG_DT_FORMAT)
                 if inst.message_sent_at else "—"),
                (inst.due_at.strftime(_DELIVERY_LOG_DT_FORMAT)
                 if inst.due_at else "—"),
            ])
        spreadsheet_id = (str(await settings.get("sync.spreadsheet_id"))
                          or get_settings().google_sheets_spreadsheet_id)
        try:
            client = SheetsClient(
                get_settings().google_sheets_credentials_file, spreadsheet_id)
            client.append_rows(DELIVERY_LOG_SHEET_NAME, rows, DELIVERY_LOG_HEADER)
        except Exception:  # noqa: BLE001 — недоступность Sheets не роняет планировщик
            logger.exception(
                "delivery_log_job: не удалось дописать %d строк(и) в лист %r — "
                "строки будут повторно взяты на следующем интервале",
                len(rows), DELIVERY_LOG_SHEET_NAME)
            return
        now = datetime.utcnow()
        for inst in instances:
            inst.sheet_logged_at = now
        await session.commit()


STATUS_HISTORY_SHEET_NAME = "История статусов"
STATUS_HISTORY_HEADER = ["Задача", "Был статус", "Стал статус", "Кто", "Когда"]


async def status_history_job(bot, session_factory) -> None:
    """Job-обработчик листа «История статусов» (Часть Д; регистрируется
    SchedulerService.register_status_history_job — Task 20). По строке на
    КАЖДУЮ запись TaskLog без фильтра по статусу — полная хронология смен
    статуса, в отличие от настраиваемого списка уведомляемых статусов
    Части Г. Идемпотентность — через TaskLog.sheet_logged_at (отдельный
    флаг: НЕ owner_notified_at Части Г и НЕ TaskInstance.sheet_logged_at
    Части Б). Ничего не делает при status_history_log.enabled=False.

    Ошибка Google Sheets API логируется, ни одна запись НЕ помечается —
    весь пакет уйдёт повторно на следующем интервале (как delivery_log_job)."""
    from bot.database.db import async_session_factory
    from bot.database.repositories.task_repository import TaskRepository
    from bot.database.repositories.user_repository import UserRepository
    from bot.services.status_notification_service import resolve_log_actor

    factory = session_factory or async_session_factory
    async with factory() as session:
        settings = SettingService(session)
        if not bool(await settings.get("status_history_log.enabled")):
            return
        task_repo = TaskRepository(session)
        users = UserRepository(session)
        logs = await task_repo.get_unlogged_status_logs()
        if not logs:
            return
        rows: list[list] = []
        for log in logs:
            inst = await task_repo.get_instance(log.task_instance_id)
            title = inst.title_snapshot if inst else f"задача #{log.task_instance_id}"
            rows.append([
                title,
                log.old_status or "—",       # NULL — первое создание записи
                log.new_status or "—",
                await resolve_log_actor(users, log),
                (log.created_at.strftime(_DELIVERY_LOG_DT_FORMAT)
                 if log.created_at else "—"),
            ])
        spreadsheet_id = (str(await settings.get("sync.spreadsheet_id"))
                          or get_settings().google_sheets_spreadsheet_id)
        try:
            client = SheetsClient(
                get_settings().google_sheets_credentials_file, spreadsheet_id)
            client.append_rows(STATUS_HISTORY_SHEET_NAME, rows, STATUS_HISTORY_HEADER)
        except Exception:  # noqa: BLE001 — недоступность Sheets не роняет планировщик
            logger.exception(
                "status_history_job: не удалось дописать %d строк(и) в лист %r — "
                "строки будут повторно взяты на следующем интервале",
                len(rows), STATUS_HISTORY_SHEET_NAME)
            return
        now = datetime.utcnow()
        for log in logs:
            log.sheet_logged_at = now
        await session.commit()
