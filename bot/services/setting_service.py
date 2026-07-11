import json
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.repositories.setting_repository import SettingRepository
from bot.services.audit_service import AuditService


@dataclass(frozen=True)
class SettingDef:
    key: str
    value_type: type          # int | bool | str | (list/dict => "json")
    default: object
    category: str
    description: str = ""
    min_: int | None = None
    max_: int | None = None
    choices: tuple[str, ...] | None = None
    is_editable: bool = True


def _defs() -> list[SettingDef]:
    return [
        # --- general ---
        SettingDef("general.timezone", str, "Europe/Moscow", "general", "Часовой пояс"),
        SettingDef("general.datetime_format", str, "%d.%m.%Y %H:%M", "general"),
        SettingDef("general.language", str, "ru", "general", choices=("ru",)),
        SettingDef("general.group_chat_id", int, 0, "general", "ID основной группы"),
        SettingDef("general.bot_enabled", bool, True, "general"),
        SettingDef("general.maintenance_mode", bool, False, "general"),
        SettingDef("general.page_size", int, 10, "general", min_=5, max_=50),
        SettingDef("general.max_comment_length", int, 500, "general", min_=10, max_=4000),
        SettingDef("general.max_question_length", int, 1000, "general", min_=10, max_=4000),
        SettingDef("general.max_task_title_length", int, 255, "general", min_=10, max_=255),
        SettingDef("general.log_retention_days", int, 90, "general", min_=7, max_=3650),
        SettingDef("general.verbose_business_logging", bool, False, "general"),
        SettingDef("general.telegram_retry_count", int, 3, "general", min_=0, max_=10),
        SettingDef("general.telegram_retry_intervals", object, [30, 300, 1800], "general"),
        # --- article_check ---
        SettingDef("article_check.batch_size", int, 15, "article_check", min_=1, max_=100),
        SettingDef("article_check.batch_size_min", int, 5, "article_check"),
        SettingDef("article_check.batch_size_max", int, 50, "article_check"),
        SettingDef("article_check.source", str, "google_sheets", "article_check",
                   choices=("google_sheets", "manual")),
        SettingDef("article_check.sheet_name", str, "Articles", "article_check"),
        SettingDef("article_check.show_product_name", bool, True, "article_check"),
        SettingDef("article_check.show_extra_metrics", bool, False, "article_check"),
        SettingDef("article_check.sort_order", str, "sort_order", "article_check",
                   choices=("sort_order", "article", "name")),
        SettingDef("article_check.allow_finish_with_open_questions", bool, True, "article_check"),
        SettingDef("article_check.require_action_for_action_required", bool, True, "article_check"),
        SettingDef("article_check.default_next_check_days", int, 3, "article_check", min_=1, max_=365),
        SettingDef("article_check.next_check_min_days", int, 1, "article_check"),
        SettingDef("article_check.next_check_max_days", int, 60, "article_check"),
        SettingDef("article_check.allow_prev_batch", bool, True, "article_check"),
        SettingDef("article_check.allow_change_result", bool, True, "article_check"),
        SettingDef("article_check.allow_manual_article_add", bool, False, "article_check"),
        SettingDef("article_check.include_archived", bool, False, "article_check"),
        SettingDef("article_check.session_list_change_policy", str, "keep_snapshot",
                   "article_check", choices=("keep_snapshot",)),
        SettingDef("article_check.allow_finish_batch_with_pending", bool, False, "article_check"),
        # --- approval ---
        SettingDef("approval.timeout_hours", int, 24, "approval", min_=1, max_=168),
        SettingDef("approval.auto_approve_enabled", bool, True, "approval"),
        SettingDef("approval.approvers_mode", str, "first", "approval",
                   choices=("owner", "partner", "both", "first")),
        SettingDef("approval.require_two_approvals", bool, False, "approval"),
        SettingDef("approval.topic_key", str, "owners_decisions", "approval"),
        SettingDef("approval.send_private", bool, False, "approval"),
        SettingDef("approval.notify_on_approve", bool, True, "approval"),
        SettingDef("approval.notify_on_auto_approve", bool, True, "approval"),
        SettingDef("approval.allow_return_to_work", bool, True, "approval"),
        SettingDef("approval.return_comment_required", bool, True, "approval"),
        # --- reminders ---
        SettingDef("reminders.first_after_hours", int, 3, "reminders", min_=1, max_=48),
        SettingDef("reminders.second_after_hours", int, 6, "reminders", min_=1, max_=96),
        SettingDef("reminders.extra_enabled", bool, False, "reminders"),
        SettingDef("reminders.repeat_interval_hours", int, 4, "reminders", min_=1, max_=48),
        SettingDef("reminders.max_count", int, 2, "reminders", min_=0, max_=10),
        SettingDef("reminders.targets", object, ["topic"], "reminders"),
        SettingDef("reminders.quiet_hours_start", str, "22:00", "reminders"),
        SettingDef("reminders.quiet_hours_end", str, "08:00", "reminders"),
        SettingDef("reminders.shift_night_to_morning", bool, True, "reminders"),
        SettingDef("reminders.text_template", str,
                   "⏰ Напоминание: задача «{title}» не завершена", "reminders"),
        SettingDef("reminders.overdue_enabled", bool, True, "reminders"),
        SettingDef("reminders.overdue_after_hours", int, 24, "reminders", min_=1, max_=168),
        SettingDef("reminders.escalation_enabled", bool, False, "reminders"),
        SettingDef("reminders.escalation_targets", object, ["owner"], "reminders"),
        # --- questions ---
        SettingDef("questions.default_receiver_user_id", int, 0, "questions"),
        SettingDef("questions.fallback_receiver_user_id", int, 0, "questions"),
        SettingDef("questions.escalation_hours", int, 4, "questions", min_=1, max_=72),
        SettingDef("questions.escalation_receiver_user_id", int, 0, "questions"),
        SettingDef("questions.notify_asker_on_answer", bool, True, "questions"),
        SettingDef("questions.allow_complete_with_open_questions", bool, True, "questions"),
        SettingDef("questions.route_by_topic", object, {}, "questions"),
        SettingDef("questions.route_by_category", object, {}, "questions"),
        # --- reports ---
        SettingDef("reports.weekday", int, 6, "reports", min_=0, max_=6),
        SettingDef("reports.time", str, "20:00", "reports"),
        SettingDef("reports.period_days", int, 7, "reports", min_=1, max_=31),
        SettingDef("reports.topic_key", str, "reports", "reports"),
        # ВАЖНО: в отличие от прочих *_receiver_user_id (см. questions.* выше),
        # здесь хранятся СЫРЫЕ Telegram chat_id, а не users.id. Получатели —
        # внешние адресаты отчёта, необязательно зарегистрированные в боте
        # пользователи, поэтому резолва через UserRepository.get_by_id() нет
        # и не должно появиться (осознанное решение, подтверждено владельцем
        # продукта в ревью Task 21).
        SettingDef(
            "reports.private_receiver_ids", object, [], "reports",
            description=(
                "Список СЫРЫХ Telegram chat_id для отправки отчёта внешним "
                "получателям (не users.id!) — в отличие от других "
                "*_receiver_user_id настроек, эти получатели не обязаны быть "
                "зарегистрированными пользователями бота."
            ),
        ),
        SettingDef("reports.show_overdue", bool, True, "reports"),
        SettingDef("reports.show_auto_approved", bool, True, "reports"),
        SettingDef("reports.show_problem_articles", bool, True, "reports"),
        SettingDef("reports.show_open_questions", bool, True, "reports"),
        SettingDef("reports.show_expired_checks", bool, True, "reports"),
        SettingDef("reports.show_per_employee", bool, True, "reports"),
        SettingDef("reports.format", str, "full", "reports", choices=("short", "full")),
        # --- sync ---
        SettingDef("sync.spreadsheet_id", str, "", "sync"),
        SettingDef("sync.sheet_users", str, "Users", "sync"),
        SettingDef("sync.sheet_topics", str, "Topics", "sync"),
        SettingDef("sync.sheet_tasks", str, "Tasks_Config", "sync"),
        SettingDef("sync.auto_enabled", bool, False, "sync"),
        SettingDef("sync.interval_minutes", int, 60, "sync", min_=5, max_=1440),
        SettingDef("sync.conflict_policy", str, "admin_wins", "sync",
                   choices=("admin_wins", "sheets_wins")),
        SettingDef("sync.dry_run_default", bool, True, "sync"),
        # --- internal ---
        SettingDef("internal.settings_version", int, 1, "internal", is_editable=False),
    ]


SETTINGS_REGISTRY: dict[str, SettingDef] = {d.key: d for d in _defs()}

_cache: dict[str, object] = {}


def invalidate(key: str | None = None) -> None:
    if key is None:
        _cache.clear()
    else:
        _cache.pop(key, None)


class SettingService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = SettingRepository(session)
        self.audit = AuditService(session)

    def _def(self, key: str) -> SettingDef:
        if key not in SETTINGS_REGISTRY:
            raise KeyError(f"Неизвестная настройка: {key}")
        return SETTINGS_REGISTRY[key]

    async def get(self, key: str, default: object = None) -> object:
        d = self._def(key)
        if key in _cache:
            return _cache[key]
        row = await self.repo.get(key)
        value = json.loads(row.value_json) if row else (
            default if default is not None else d.default)
        _cache[key] = value
        return value

    async def get_typed(self, key: str, expected_type: type) -> object:
        value = await self.get(key)
        if expected_type is bool and not isinstance(value, bool):
            raise TypeError(f"{key}: ожидался bool")
        if expected_type is int and (isinstance(value, bool) or not isinstance(value, int)):
            raise TypeError(f"{key}: ожидался int")
        if expected_type is str and not isinstance(value, str):
            raise TypeError(f"{key}: ожидался str")
        return value

    def validate(self, key: str, value: object) -> object:
        d = self._def(key)
        if d.value_type is int:
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"{key}: нужно целое число")
            if d.min_ is not None and value < d.min_:
                raise ValueError(f"{key}: минимум {d.min_}")
            if d.max_ is not None and value > d.max_:
                raise ValueError(f"{key}: максимум {d.max_}")
        elif d.value_type is bool:
            if not isinstance(value, bool):
                raise ValueError(f"{key}: нужно значение да/нет")
        elif d.value_type is str:
            if not isinstance(value, str):
                raise ValueError(f"{key}: нужна строка")
            if d.choices and value not in d.choices:
                raise ValueError(f"{key}: допустимо {', '.join(d.choices)}")
        else:  # json
            json.dumps(value)  # должно сериализоваться
        return value

    async def set(self, key: str, value: object, actor_user_id: int | None) -> None:
        d = self._def(key)
        if not d.is_editable:
            raise PermissionError(f"Настройка {key} не редактируется")
        self.validate(key, value)
        old = await self.get(key)
        type_name = {int: "int", bool: "bool", str: "str"}.get(d.value_type, "json")
        await self.repo.upsert(key, json.dumps(value), type_name, d.category,
                               actor_user_id, is_editable=d.is_editable)
        await self.audit.log(actor_user_id, "setting.set",
                             setting_key=key, old_value=old, new_value=value)
        await self._bump_version()
        invalidate(key)

    async def reset(self, key: str, actor_user_id: int | None) -> None:
        await self.set(key, self._def(key).default, actor_user_id)

    async def list_by_category(self, category: str) -> list[SettingDef]:
        return [d for d in SETTINGS_REGISTRY.values()
                if d.category == category and d.is_editable]

    async def export_settings(self) -> dict:
        return {k: await self.get(k) for k, d in SETTINGS_REGISTRY.items() if d.is_editable}

    async def import_settings(self, data: dict, actor_user_id: int | None,
                              dry_run: bool) -> dict:
        applied, rejected = [], []
        for key, value in data.items():
            d = SETTINGS_REGISTRY.get(key)
            if d is None or not d.is_editable:
                rejected.append(key)      # секреты (.env) и internal.* сюда не попадают
                continue
            try:
                self.validate(key, value)
            except ValueError:
                rejected.append(key)
                continue
            if not dry_run:
                await self.set(key, value, actor_user_id)
            applied.append(key)
        return {"applied": applied, "rejected": rejected, "dry_run": dry_run}

    async def current_version(self) -> int:
        return int(await self.get("internal.settings_version"))

    async def _bump_version(self) -> None:
        row = await self.repo.get("internal.settings_version")
        current = json.loads(row.value_json) if row else 1
        await self.repo.upsert("internal.settings_version", json.dumps(current + 1),
                               "int", "internal", None, is_editable=False)
        invalidate("internal.settings_version")
