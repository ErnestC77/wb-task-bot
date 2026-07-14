"""Раздел админ-панели «✅ Шаблоны задач» (Task 27).

Навигация: список шаблонов (пагинация general.page_size, неактивные помечены
«(неактивен)») -> карточка со ВСЕМИ полями `TaskConfig` + «Ближайший запуск:
{next_run_at}» -> операции: создать (мастер FSM: title -> description ->
responsible -> topic -> scenario -> schedule_type -> interval/value ->
time -> due_time -> need_approval -> remind hours -> question_receiver),
изменить любое поле из whitelist `EDITABLE_FIELDS` (редактирование ПО ИНДЕКСУ
в `FIELD_LIST_FOR_UI`, не по имени поля из callback), активировать/
деактивировать (деактивация — ОПАСНАЯ операция, только через
`AdminService.confirm_token`, по аналогии с Tasks 25/26), «📋 Клонировать»
(`clone_config`, копия неактивна), «▶ Запустить сейчас» (`manual_run_config`,
через `confirm_token`).

`manual_run_config` — брифовая ссылка "делегирует в manual_run_config из Task
34" указывает на задачу, которой на момент реализации Task 27 ещё нет
(проверено: ни brief, ни отчёт Task 34 не существуют в .superpowers/sdd/).
Функция поэтому определена ЗДЕСЬ как часть Task 27 (используется кнопкой
«▶ Запустить сейчас» карточки шаблона, право tasks.manage) — будущий Task 34
(общий раздел «▶ Ручной запуск», AdminCb.s="run", право tasks.run_manual,
см. SECTION_PERMISSIONS) сможет переиспользовать/расширить эту же функцию.
Это осознанное решение, а не догадка о деталях Task 34: сигнатура и место
подобраны так, чтобы не блокировать Task 27 и не создавать конфликт имён,
если Task 34 определит собственный, более полный manual_run_config.

Навигация внутри раздела использует СОБСТВЕННЫЙ `CfgCb` (prefix="tc"), а НЕ
`AdminCb` — см. docstring в bot/keyboards/admin/task_configs.py. Поэтому,
как и в разделах "Пользователи" (Task 25) и "Темы" (Task 26), КАЖДЫЙ callback
этого раздела (`handle_cfg_callback`, единая точка входа для всех `CfgCb`) и
КАЖДОЕ FSM-продолжение сообщением заново проверяет actor и право
tasks.manage — resolve_admin для CfgCb.filter() не срабатывает.

Изменение полей шаблона — ИСКЛЮЧИТЕЛЬНО через `apply_field_edit`, который
проверяет `field` по whitelist `EDITABLE_FIELDS` ДО `setattr` (единственное
место в этом модуле, где используется generic `setattr` по строковому
ключу — оправдано тем, что ключ уже прошёл проверку на членство в whitelist,
см. комментарий в самой функции, код которой дан брифом дословно).

Task 27 требование (важно для будущего Task 28 «Расписания»): любое изменение
поля, влияющего на `compute_next_run` (см. `SCHEDULE_AFFECTING_FIELDS`), а
также активация/деактивация шаблона, ОБЯЗАНЫ вызывать
`SchedulerService.rebuild_config_job(config_id)` — та сама делает
`remove_job` перед `add_job` (идемпотентно, см. Task 12) и обнуляет
`next_run_at`/снимает job, если шаблон стал неактивным. Без этого вызова
старый job планировщика оставался бы висеть на старом расписании после
правки — см. `_toggle_active`, `_start_edit_field`, `handle_cfg_edit_message`.
"""
import re
from datetime import date, datetime

from aiogram import Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select

from bot.database.models import Role, ScheduleType, TaskConfig, TaskInstance, TaskScenario, User
from bot.database.repositories.task_repository import TaskRepository
from bot.database.repositories.topic_repository import TopicRepository
from bot.database.repositories.user_repository import UserRepository
from bot.keyboards.admin.confirm import confirm_keyboard
from bot.keyboards.admin.main import AdminCb, admin_menu_keyboard
from bot.keyboards.admin.task_configs import (
    CfgCb, cancel_creation_keyboard, config_card_keyboard, configs_list_keyboard,
    creation_choice_keyboard, fields_list_keyboard,
)
from bot.services.admin_service import AdminService
from bot.services.audit_service import AuditService
from bot.services.setting_service import SettingService
from bot.services.user_service import UserService
from bot.states.admin_states import AdminStates
from bot.utils.html_utils import code, html_escape
from bot.utils.validation import validate_int, validate_time_str

router = Router(name=__name__)

# --------------------------------------------------------------------------
# Доменные функции (тестируются напрямую, без callback-обвязки) — сигнатуры и
# тела EDITABLE_FIELDS/apply_field_edit/clone_config заданы брифом Task 27
# дословно.
# --------------------------------------------------------------------------

EDITABLE_FIELDS = {
    "title", "description", "responsible_user_id", "responsible_role", "topic_id",
    "schedule_type", "schedule_value", "schedule_interval", "time", "due_time",
    "first_run_date", "run_on_weekends", "skip_holidays", "need_approval",
    "remind_after_hours", "second_remind_after_hours", "question_receiver_user_id",
    "is_active",
}


async def apply_field_edit(session, actor: User, config_id: int, field: str,
                           value) -> tuple[bool, str]:
    if field not in EDITABLE_FIELDS:
        return False, "Поле запрещено к редактированию"
    repo = TaskRepository(session)
    cfg = await repo.get_config(config_id)
    if cfg is None:
        return False, "Шаблон не найден"
    old = getattr(cfg, field)
    setattr(cfg, field, value)             # безопасно: field уже прошёл whitelist
    await session.flush()
    await AuditService(session).log(actor.id, "task_config.edit",
                                    entity_type="task_config", entity_id=str(config_id),
                                    setting_key=field, old_value=str(old), new_value=str(value))
    return True, "Сохранено ✅"


async def clone_config(session, actor: User, config_id: int) -> TaskConfig:
    repo = TaskRepository(session)
    src = await repo.get_config(config_id)
    if src is None:
        raise ValueError("Шаблон не найден")
    existing = {c.external_task_id for c in await repo.get_active_configs()}
    n = 1
    new_id = f"{src.external_task_id}_copy_{n}"
    while new_id in existing:
        n += 1
        new_id = f"{src.external_task_id}_copy_{n}"
    data = {col.name: getattr(src, col.name) for col in src.__table__.columns
            if col.name not in ("id", "external_task_id", "created_at", "updated_at")}
    data["external_task_id"] = new_id
    data["is_active"] = False
    clone = await repo.upsert_config(data)
    await session.flush()
    await AuditService(session).log(actor.id, "task_config.clone",
                                    entity_type="task_config", entity_id=str(clone.id),
                                    old_value=config_id)
    return clone


async def manual_run_config(session, bot, scheduler_svc, actor: User,
                            config_id: int) -> TaskInstance | None:
    """Ручной запуск шаблона вне расписания («▶ Запустить сейчас»).

    НЕ трогает next_run_at/job планового расписания конфига (это отдельная
    сущность — плановый прогон, см. `SchedulerService.run_config`) — ручной
    запуск создаёт ДОПОЛНИТЕЛЬНЫЙ `TaskInstance` "прямо сейчас", идемпотентность
    которого по-прежнему держит UniqueConstraint(config_id, scheduled_at) у
    TaskInstance (см. TaskRepository.create_instance_idempotent)."""
    from bot.services.delivery_service import DeliveryService
    from bot.services.task_service import TaskService

    repo = TaskRepository(session)
    config = await repo.get_config(config_id)
    if config is None:
        raise ValueError("Шаблон не найден")
    scheduled_at = datetime.utcnow().replace(second=0, microsecond=0)
    inst = await TaskService(session, bot).create_instance_for(config, scheduled_at)
    if inst is not None:
        scheduler = scheduler_svc.scheduler if scheduler_svc is not None else None
        await DeliveryService(session, bot, scheduler).send_task_message(inst)
        if scheduler_svc is not None:
            await scheduler_svc.register_instance_jobs(inst, session=session)
    await AuditService(session).log(
        actor.id, "task_config.manual_run", entity_type="task_config",
        entity_id=str(config_id), result="ok" if inst is not None else "duplicate")
    return inst


# --------------------------------------------------------------------------
# Whitelist-редактирование по индексу (не по имени поля из callback)
# --------------------------------------------------------------------------

FIELD_TITLES: dict[str, str] = {
    "title": "Название",
    "description": "Описание",
    "responsible_user_id": "Ответственный (user_id)",
    "responsible_role": "Ответственная роль",
    "topic_id": "Тема (topic_id)",
    "schedule_type": "Тип расписания",
    "schedule_value": "Значение расписания",
    "schedule_interval": "Интервал (дней)",
    "time": "Время создания",
    "due_time": "Срок (due_time)",
    "first_run_date": "Дата первого запуска",
    "run_on_weekends": "Запуск в выходные",
    "skip_holidays": "Пропуск праздников",
    "need_approval": "Нужно подтверждение",
    "remind_after_hours": "Напоминание через (ч)",
    "second_remind_after_hours": "Второе напоминание через (ч)",
    "question_receiver_user_id": "Получатель вопросов (user_id)",
}

FIELD_HINTS: dict[str, str] = {
    "responsible_user_id": "Введите ID пользователя (число) или «-», чтобы очистить:",
    "topic_id": "Введите ID темы (число) или «-», чтобы очистить:",
    "question_receiver_user_id": "Введите ID пользователя (число) или «-» для получателя по умолчанию:",
    "responsible_role": "Введите код роли (owner/partner/manager_wb/logistic) или «-»:",
    "schedule_type": "Введите тип расписания (daily/weekly/monthly/every_n_days/cron):",
    "schedule_value": "Введите значение расписания или «-»:",
    "schedule_interval": "Введите интервал в днях (целое число) или «-»:",
    "time": "Введите время в формате ЧЧ:ММ или «-»:",
    "due_time": "Введите срок в формате ЧЧ:ММ или «-»:",
    "first_run_date": "Введите дату в формате ГГГГ-ММ-ДД или «-»:",
    "remind_after_hours": "Введите часы (целое число) или «-» для значения по умолчанию:",
    "second_remind_after_hours": "Введите часы (целое число) или «-» для значения по умолчанию:",
}
DEFAULT_FIELD_HINT = "Введите новое значение:"

# is_active сознательно исключён из общего экрана «Изменить поле» — активация/
# деактивация шаблона идёт ТОЛЬКО через отдельную кнопку toggle (деактивация —
# ОПАСНАЯ операция, confirm_token), а не через безусловный мгновенный instant-
# toggle, которым здесь обрабатываются остальные bool-поля (см. BOOL_FIELDS).
# is_active при этом остаётся в EDITABLE_FIELDS (whitelist apply_field_edit) —
# ровно как задано брифом.
FIELD_LIST_FOR_UI: list[str] = sorted(EDITABLE_FIELDS - {"is_active"})

# Bool-поля редактируются мгновенным toggle (без FSM, без риска опечатки
# "да"/"нет") — единственное исключение "is_active" см. выше.
BOOL_FIELDS: frozenset[str] = frozenset({"run_on_weekends", "skip_holidays", "need_approval"})

# Поля, влияющие на compute_next_run (bot/services/scheduler_service.py) —
# изменение любого из них ОБЯЗАНО пересобрать job шаблона (Task 27 требование,
# важное для будущего Task 28 «Расписания»). first_run_date включён с запасом
# (сейчас compute_next_run его не использует, но безопасно и дёшево пересчитать
# next_run_at на всякий случай — rebuild_config_job идемпотентен).
SCHEDULE_AFFECTING_FIELDS: frozenset[str] = frozenset({
    "schedule_type", "schedule_value", "schedule_interval", "time",
    "run_on_weekends", "first_run_date",
})

ROLE_VALUES: frozenset[str] = frozenset(r.value for r in Role)
SCHEDULE_TYPE_VALUES: frozenset[str] = frozenset(s.value for s in ScheduleType)
SCENARIO_VALUES: frozenset[str] = frozenset(s.value for s in TaskScenario)


def _parse_date(raw: str) -> date:
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError:
        raise ValueError("Формат даты: ГГГГ-ММ-ДД, например 2026-07-10") from None


async def parse_field_raw(session, field: str, raw: str) -> object:
    """Преобразует сырой текст сообщения в типизированное значение поля
    (используется ТОЛЬКО перед вызовом apply_field_edit — сам apply_field_edit
    принимает уже готовое значение, как задано брифом)."""
    raw = raw.strip()
    empty = raw in ("", "-")
    if field == "title":
        if not raw:
            raise ValueError("Название не может быть пустым")
        return raw
    if field == "description":
        return None if empty else raw
    if field in ("responsible_user_id", "question_receiver_user_id"):
        if empty:
            return None
        user_id = validate_int(raw)
        user = await UserRepository(session).get_by_id(user_id)
        if user is None or not user.is_active:
            raise ValueError("Пользователь должен существовать и быть активным")
        return user_id
    if field == "responsible_role":
        if empty:
            return None
        if raw not in ROLE_VALUES:
            raise ValueError("Допустимые роли: " + ", ".join(sorted(ROLE_VALUES)))
        return raw
    if field == "topic_id":
        if empty:
            return None
        topic_id = validate_int(raw)
        topic = await TopicRepository(session).get_by_id(topic_id)
        if topic is None:
            raise ValueError("Тема не найдена")
        return topic_id
    if field == "schedule_type":
        if raw not in SCHEDULE_TYPE_VALUES:
            raise ValueError("Допустимые типы: " + ", ".join(sorted(SCHEDULE_TYPE_VALUES)))
        return raw
    if field == "schedule_value":
        return None if empty else raw
    if field == "schedule_interval":
        return None if empty else validate_int(raw, 1)
    if field in ("time", "due_time"):
        return None if empty else validate_time_str(raw)
    if field == "first_run_date":
        return None if empty else _parse_date(raw)
    if field in ("remind_after_hours", "second_remind_after_hours"):
        return None if empty else validate_int(raw, 0)
    raise ValueError("Поле недоступно для текстового редактирования")


# --------------------------------------------------------------------------
# Отображение
# --------------------------------------------------------------------------

async def render_config_card(session, cfg: TaskConfig) -> str:
    responsible = cfg.responsible_user.name if cfg.responsible_user else (cfg.responsible_role or "—")
    topic_name = cfg.topic.topic_name if cfg.topic else "—"
    receiver = "—"
    if cfg.question_receiver_user_id:
        receiver_user = await UserRepository(session).get_by_id(cfg.question_receiver_user_id)
        receiver = receiver_user.name if receiver_user else str(cfg.question_receiver_user_id)
    lines = [
        f"✅ {html_escape(cfg.title)}",
        f"ID: {code(cfg.external_task_id)}",
        f"Описание: {html_escape(cfg.description) if cfg.description else '—'}",
        f"Сценарий: {html_escape(cfg.scenario)}",
        f"Ответственный: {html_escape(responsible)}",
        f"Тема: {html_escape(topic_name)}",
        f"Тип расписания: {html_escape(cfg.schedule_type)}",
        f"Значение расписания: {html_escape(cfg.schedule_value) if cfg.schedule_value else '—'}",
        f"Интервал (дней): {cfg.schedule_interval if cfg.schedule_interval is not None else '—'}",
        f"Время: {cfg.time.strftime('%H:%M') if cfg.time else '—'}",
        f"Срок (due_time): {cfg.due_time.strftime('%H:%M') if cfg.due_time else '—'}",
        f"Дата первого запуска: {cfg.first_run_date.isoformat() if cfg.first_run_date else '—'}",
        f"Запуск в выходные: {'да' if cfg.run_on_weekends else 'нет'}",
        f"Пропуск праздников: {'да' if cfg.skip_holidays else 'нет'}",
        f"Нужно подтверждение: {'да' if cfg.need_approval else 'нет'}",
        f"Напоминание через (ч): {cfg.remind_after_hours if cfg.remind_after_hours is not None else '—'}",
        f"Второе напоминание через (ч): {cfg.second_remind_after_hours if cfg.second_remind_after_hours is not None else '—'}",
        f"Получатель вопросов: {html_escape(receiver)}",
        f"Статус: {'активен' if cfg.is_active else 'неактивен'}",
        f"Ближайший запуск: {cfg.next_run_at.strftime('%d.%m.%Y %H:%M') if cfg.next_run_at else '—'}",
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------
# actor-check (каждый CfgCb-callback и каждое FSM-сообщение проверяют заново —
# resolve_admin из main.py срабатывает только для AdminCb, не для CfgCb)
# --------------------------------------------------------------------------

async def _resolve_actor(callback: CallbackQuery, session) -> tuple[User | None, AdminService | None]:
    actor = await UserService(session).get_actor(callback.from_user.id)
    svc = AdminService(session)
    if actor is None or not await svc.permissions.has_permission(actor, "tasks.manage"):
        await callback.answer("Недостаточно прав", show_alert=True)
        return None, None
    return actor, svc


async def _resolve_actor_message(message: Message, session) -> User | None:
    actor = await UserService(session).get_actor(message.from_user.id)
    svc = AdminService(session)
    if actor is None or not await svc.permissions.has_permission(actor, "tasks.manage"):
        await message.answer("Недостаточно прав")
        return None
    return actor


def _extract_scheduler_svc(dispatcher):
    """Тот же паттерн получения SchedulerService из dispatcher.workflow_data,
    что и bot/handlers/admin/settings.py (Task 24: dp["scheduler"] = scheduler
    в bot/main.py, scheduler.wb_service — SchedulerService)."""
    if dispatcher is None:
        return None
    scheduler_obj = getattr(dispatcher, "workflow_data", {}).get("scheduler")
    return getattr(scheduler_obj, "wb_service", None)


# --------------------------------------------------------------------------
# Список / карточка
# --------------------------------------------------------------------------

async def _show_menu(callback: CallbackQuery, actor: User, svc: AdminService) -> None:
    allowed = await svc.visible_sections(actor)
    await callback.message.edit_text("🛠 Админ-панель", reply_markup=admin_menu_keyboard(allowed))
    await callback.answer()


async def _show_configs_list(callback: CallbackQuery, session, page: int) -> None:
    configs = sorted(await TaskRepository(session).get_all_configs(include_inactive=True),
                     key=lambda c: c.title)
    settings_svc = SettingService(session)
    page_size = max(1, int(await settings_svc.get("general.page_size")))
    total_pages = max(1, -(-len(configs) // page_size))
    page = min(max(1, page), total_pages)
    start = (page - 1) * page_size
    entries = [(c.id, c.title if c.is_active else f"{c.title} (неактивен)")
               for c in configs[start:start + page_size]]
    await callback.message.edit_text(
        "✅ Шаблоны задач", reply_markup=configs_list_keyboard(entries, page, total_pages))
    await callback.answer()


async def _show_card(callback: CallbackQuery, session, config_id: int) -> None:
    cfg = await TaskRepository(session).get_config(config_id)
    if cfg is None:
        await callback.answer("Шаблон не найден", show_alert=True)
        return
    text = await render_config_card(session, cfg)
    await callback.message.edit_text(text, reply_markup=config_card_keyboard(cfg.id, cfg.is_active))
    await callback.answer()


async def _show_fields(callback: CallbackQuery, session, config_id: int, page: int) -> None:
    cfg = await TaskRepository(session).get_config(config_id)
    if cfg is None:
        await callback.answer("Шаблон не найден", show_alert=True)
        return
    settings_svc = SettingService(session)
    page_size = max(1, int(await settings_svc.get("general.page_size")))
    total_pages = max(1, -(-len(FIELD_LIST_FOR_UI) // page_size))
    page = min(max(1, page), total_pages)
    start = (page - 1) * page_size
    entries: list[tuple[int, str]] = []
    for offset, field in enumerate(FIELD_LIST_FOR_UI[start:start + page_size]):
        idx = start + offset
        value = getattr(cfg, field)
        mark = "✅" if field in BOOL_FIELDS and value else ("▫" if field in BOOL_FIELDS else None)
        label = (f"{mark} {FIELD_TITLES.get(field, field)}" if mark is not None
                else f"{FIELD_TITLES.get(field, field)} = {value if value is not None else '—'}")
        entries.append((idx, label[:60]))
    await callback.message.edit_text(
        "✏ Изменить поле", reply_markup=fields_list_keyboard(config_id, entries, page, total_pages))
    await callback.answer()


# --------------------------------------------------------------------------
# Редактирование одного поля (по индексу в FIELD_LIST_FOR_UI)
# --------------------------------------------------------------------------

async def _start_edit_field(callback: CallbackQuery, session, state: FSMContext | None,
                            actor: User, svc: AdminService, scheduler_svc,
                            config_id: int, idx: int) -> None:
    if not 0 <= idx < len(FIELD_LIST_FOR_UI):
        await callback.answer("Неизвестное поле", show_alert=True)
        return
    field = FIELD_LIST_FOR_UI[idx]
    cfg = await TaskRepository(session).get_config(config_id)
    if cfg is None:
        await callback.answer("Шаблон не найден", show_alert=True)
        return
    if field in BOOL_FIELDS:
        old = getattr(cfg, field)
        ok, msg = await apply_field_edit(session, actor, config_id, field, not old)
        await session.commit()             # коммит ДО rebuild — иначе rebuild_config_job
                                            # (своя сессия/соединение, см. SchedulerService)
                                            # может не увидеть ещё не закоммиченное поле —
                                            # тот же порядок, что и в _toggle_active выше.
        if ok and field in SCHEDULE_AFFECTING_FIELDS and scheduler_svc is not None:
            await scheduler_svc.rebuild_config_job(config_id)
        await _show_fields(callback, session, config_id, 1)
        return
    if state is None:
        await callback.answer("Недоступно", show_alert=True)
        return
    current = getattr(cfg, field)
    await state.set_state(AdminStates.waiting_cfg_edit)
    await state.update_data(config_id=config_id, field=field)
    hint = FIELD_HINTS.get(field, DEFAULT_FIELD_HINT)
    await callback.message.edit_text(
        f"Поле: {html_escape(FIELD_TITLES.get(field, field))}\n"
        f"Текущее значение: {code(current) if current is not None else '—'}\n{hint}")
    await callback.answer()


@router.message(AdminStates.waiting_cfg_edit)
async def handle_cfg_edit_message(message: Message, session, state: FSMContext,
                                  dispatcher=None) -> None:
    actor = await _resolve_actor_message(message, session)
    if actor is None:
        await state.clear()
        return
    data = await state.get_data()
    config_id = data.get("config_id")
    field = data.get("field")
    if config_id is None or field is None:
        await message.answer("Сессия редактирования утеряна, начните заново")
        await state.clear()
        return
    try:
        value = await parse_field_raw(session, field, message.text or "")
    except ValueError as exc:
        await message.answer(str(exc))
        return
    ok, msg = await apply_field_edit(session, actor, config_id, field, value)
    await session.commit()                 # коммит ДО rebuild — иначе rebuild_config_job
                                            # (своя сессия/соединение, см. SchedulerService)
                                            # может не увидеть ещё не закоммиченное поле —
                                            # тот же порядок, что и в _toggle_active выше.
    if ok and field in SCHEDULE_AFFECTING_FIELDS:
        scheduler_svc = _extract_scheduler_svc(dispatcher)
        if scheduler_svc is not None:
            await scheduler_svc.rebuild_config_job(config_id)
    await message.answer(msg)
    if ok:
        await state.clear()


# --------------------------------------------------------------------------
# Активация / деактивация (деактивация — ОПАСНАЯ операция, только через
# confirm_token, по аналогии Tasks 25/26) — обе ветки пересобирают job
# планировщика через SchedulerService.rebuild_config_job.
# --------------------------------------------------------------------------

async def _toggle_active(callback: CallbackQuery, session, actor: User, svc: AdminService,
                         scheduler_svc, config_id: int) -> None:
    cfg = await TaskRepository(session).get_config(config_id)
    if cfg is None:
        await callback.answer("Шаблон не найден", show_alert=True)
        return
    if not cfg.is_active:
        cfg.is_active = True
        await session.flush()
        await AuditService(session).log(actor.id, "task_config.activate",
                                        entity_type="task_config", entity_id=str(config_id))
        await session.commit()               # коммит ДО rebuild — иначе rebuild (своя сессия/
                                              # соединение) может не увидеть ещё не закоммиченный is_active
        if scheduler_svc is not None:
            await scheduler_svc.rebuild_config_job(config_id)
            await session.refresh(cfg)        # подхватить next_run_at, посчитанный rebuild'ом
        text = await render_config_card(session, cfg)
        await callback.message.edit_text(
            text, reply_markup=config_card_keyboard(cfg.id, True))
        await callback.answer("Активирован ✅")
        return

    async def op(session) -> None:
        # `session` — параметр (сессия ПОДТВЕРЖДАЮЩЕГО запроса), НЕ внешняя
        # переменная того же имени из _toggle_active — см. docstring
        # AdminService.confirm_token (Task 27 review fix, Critical).
        c = await TaskRepository(session).get_config(config_id)
        if c is not None:
            c.is_active = False
            await session.flush()
        await AuditService(session).log(actor.id, "task_config.deactivate",
                                        entity_type="task_config", entity_id=str(config_id))
        await session.commit()            # коммит ДО rebuild — rebuild_config_job открывает
                                           # СВОЮ сессию (scheduler_svc.session_factory) и не
                                           # увидит незакоммиченный is_active=False; handle_confirm
                                           # закоммитит эту же сессию ещё раз следом — no-op
                                           # (см. _toggle_active активации выше — тот же паттерн).
        if scheduler_svc is not None:
            await scheduler_svc.rebuild_config_job(config_id)

    token = svc.confirm_token(f"task_config.deactivate.{config_id}", op,
                              required_permission="tasks.manage", creator_actor_id=actor.id)
    await callback.message.edit_text(
        f"Деактивировать шаблон {html_escape(cfg.title)}? "
        "Плановые запуски по расписанию остановятся, job снимается с планировщика.",
        reply_markup=confirm_keyboard(token))
    await callback.answer()


# --------------------------------------------------------------------------
# Клонирование
# --------------------------------------------------------------------------

async def _do_clone(callback: CallbackQuery, session, actor: User, config_id: int) -> None:
    try:
        clone = await clone_config(session, actor, config_id)
    except ValueError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await session.commit()
    text = await render_config_card(session, clone)
    await callback.message.edit_text(
        text, reply_markup=config_card_keyboard(clone.id, clone.is_active))
    await callback.answer("Клон создан (неактивен) ✅")


# --------------------------------------------------------------------------
# «▶ Запустить сейчас» — через confirm_token (manual_run_config)
# --------------------------------------------------------------------------

async def _start_manual_run(callback: CallbackQuery, session, actor: User, svc: AdminService,
                            scheduler_svc, config_id: int) -> None:
    cfg = await TaskRepository(session).get_config(config_id)
    if cfg is None:
        await callback.answer("Шаблон не найден", show_alert=True)
        return

    async def op(session) -> None:
        # `session` — параметр (сессия ПОДТВЕРЖДАЮЩЕГО запроса), НЕ внешняя
        # переменная того же имени из _start_manual_run — см. docstring
        # AdminService.confirm_token (Task 27 review fix, Critical).
        await manual_run_config(session, callback.bot, scheduler_svc, actor, config_id)

    token = svc.confirm_token(f"task_config.manual_run.{config_id}", op,
                              required_permission="tasks.manage", creator_actor_id=actor.id)
    await callback.message.edit_text(
        f"Запустить шаблон {html_escape(cfg.title)} сейчас? "
        "Будет создана и отправлена задача вне расписания.",
        reply_markup=confirm_keyboard(token))
    await callback.answer()


# --------------------------------------------------------------------------
# Мастер создания шаблона (FSM waiting_cfg_create, шаг хранится в data["step"] —
# шаги вперемешку текстовые (title/description/schedule_value/time/due_time/
# remind_hours) и кнопочные (responsible/topic/scenario/schedule_type/
# need_approval/question_receiver), поэтому единственное FSM-состояние на
# ВЕСЬ мастер, а не по состоянию на шаг, как в Task 25 add_id/add_name/
# add_role — там все шаги текстовые/однокнопочные, здесь их вдвое больше).
# Новый шаблон всегда создаётся is_active=False (по аналогии с клоном) —
# job планировщика появляется только после явной активации через _toggle_active.
# --------------------------------------------------------------------------

CREATE_ACTIONS = frozenset({
    "create", "cr_resp", "cr_topic", "cr_scn", "cr_sched", "cr_appr", "cr_recv", "cr_cancel",
})


async def _start_create(callback: CallbackQuery, state: FSMContext | None) -> None:
    if state is None:
        await callback.answer("Недоступно", show_alert=True)
        return
    await state.set_state(AdminStates.waiting_cfg_create)
    await state.update_data(step="title")
    await callback.message.edit_text(
        "Создание шаблона задачи.\nВведите название:", reply_markup=cancel_creation_keyboard())
    await callback.answer()


async def _cancel_create(callback: CallbackQuery, state: FSMContext | None) -> None:
    if state is not None:
        await state.clear()
    await callback.message.edit_text("Создание отменено")
    await callback.answer()


def _parse_schedule_value(schedule_type: str, raw: str) -> tuple[str | None, int | None]:
    raw = raw.strip()
    if schedule_type == ScheduleType.WEEKLY:
        return str(validate_int(raw, 0, 6)), None
    if schedule_type == ScheduleType.MONTHLY:
        return str(validate_int(raw, 1, 31)), None
    if schedule_type == ScheduleType.EVERY_N_DAYS:
        return None, validate_int(raw, 1)
    if schedule_type == ScheduleType.CRON:
        if not raw:
            raise ValueError("Введите cron-выражение")
        from apscheduler.triggers.cron import CronTrigger
        try:
            CronTrigger.from_crontab(raw)
        except Exception:
            raise ValueError("Некорректное cron-выражение") from None
        return raw, None
    raise ValueError("Неизвестный тип расписания")


def _parse_remind_hours(raw: str) -> tuple[int | None, int | None]:
    raw = raw.strip()
    if raw in ("", "-"):
        return None, None
    parts = [p.strip() for p in raw.split(",")]
    if len(parts) == 1:
        return (None if parts[0] in ("", "-") else validate_int(parts[0], 0)), None
    if len(parts) == 2:
        h1 = None if parts[0] in ("", "-") else validate_int(parts[0], 0)
        h2 = None if parts[1] in ("", "-") else validate_int(parts[1], 0)
        return h1, h2
    raise ValueError("Укажите одно или два числа (через запятую) или «-»")


async def _slugify_external_id(session, title: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "_", title.strip().lower()).strip("_") or "task"
    existing = set(await session.scalars(select(TaskConfig.external_task_id)))
    candidate = base
    n = 1
    while candidate in existing:
        n += 1
        candidate = f"{base}_{n}"
    return candidate


async def _finish_create(session, actor: User, data: dict) -> TaskConfig:
    external_id = await _slugify_external_id(session, data["title"])
    payload = dict(
        external_task_id=external_id,
        title=data["title"],
        description=data.get("description"),
        scenario=data.get("scenario", TaskScenario.SIMPLE),
        responsible_user_id=data.get("responsible_user_id"),
        topic_id=data.get("topic_id"),
        schedule_type=data.get("schedule_type", ScheduleType.DAILY),
        schedule_value=data.get("schedule_value"),
        schedule_interval=data.get("schedule_interval"),
        time=data.get("time"),
        due_time=data.get("due_time"),
        need_approval=data.get("need_approval", False),
        remind_after_hours=data.get("remind_after_hours"),
        second_remind_after_hours=data.get("second_remind_after_hours"),
        question_receiver_user_id=data.get("question_receiver_user_id"),
        is_active=False,
    )
    cfg = await TaskRepository(session).upsert_config(payload)
    await AuditService(session).log(actor.id, "task_config.create", entity_type="task_config",
                                    entity_id=str(cfg.id), new_value=external_id)
    return cfg


@router.message(AdminStates.waiting_cfg_create)
async def handle_cfg_create_message(message: Message, session, state: FSMContext) -> None:
    actor = await _resolve_actor_message(message, session)
    if actor is None:
        await state.clear()
        return
    data = await state.get_data()
    step = data.get("step")
    raw = (message.text or "").strip()

    if step == "title":
        if not raw:
            await message.answer("Название не может быть пустым, введите ещё раз:")
            return
        await state.update_data(title=raw, step="description")
        await message.answer("Введите описание (или «-», чтобы пропустить):",
                             reply_markup=cancel_creation_keyboard())
        return

    if step == "description":
        await state.update_data(description=None if raw in ("", "-") else raw)
        users = await UserRepository(session).get_all(include_inactive=False)
        options = [(str(u.id), u.name) for u in users] + [("none", "Без ответственного")]
        await state.update_data(step="responsible")
        await message.answer("Выберите ответственного:",
                             reply_markup=creation_choice_keyboard("cr_resp", options))
        return

    if step == "schedule_value":
        try:
            value, interval = _parse_schedule_value(data.get("schedule_type"), raw)
        except ValueError as exc:
            await message.answer(str(exc))
            return
        await state.update_data(schedule_value=value, schedule_interval=interval, step="time")
        await message.answer(
            "Введите время создания задачи (ЧЧ:ММ) или «-» для 09:00 по умолчанию:",
            reply_markup=cancel_creation_keyboard())
        return

    if step == "time":
        try:
            value = None if raw in ("", "-") else validate_time_str(raw)
        except ValueError as exc:
            await message.answer(str(exc))
            return
        await state.update_data(time=value, step="due_time")
        await message.answer("Введите срок due_time (ЧЧ:ММ) или «-», чтобы не задавать:",
                             reply_markup=cancel_creation_keyboard())
        return

    if step == "due_time":
        try:
            value = None if raw in ("", "-") else validate_time_str(raw)
        except ValueError as exc:
            await message.answer(str(exc))
            return
        await state.update_data(due_time=value, step="need_approval")
        await message.answer("Нужно подтверждение выполнения?",
                             reply_markup=creation_choice_keyboard(
                                 "cr_appr", [("1", "Да"), ("0", "Нет")]))
        return

    if step == "remind_hours":
        try:
            h1, h2 = _parse_remind_hours(raw)
        except ValueError as exc:
            await message.answer(str(exc))
            return
        await state.update_data(remind_after_hours=h1, second_remind_after_hours=h2,
                                step="question_receiver")
        users = await UserRepository(session).get_all(include_inactive=False)
        options = [(str(u.id), u.name) for u in users] + [("none", "По умолчанию")]
        await message.answer("Кому направлять вопросы по этой задаче?",
                             reply_markup=creation_choice_keyboard("cr_recv", options))
        return

    await message.answer("Сейчас нужно выбрать вариант кнопкой, а не текстом.")


async def _cr_pick_responsible(callback: CallbackQuery, session, state: FSMContext, k: str | None) -> None:
    data = await state.get_data()
    if data.get("step") != "responsible":
        await callback.answer("Некорректный шаг мастера", show_alert=True)
        return
    responsible_user_id = None if k in (None, "none") else int(k)
    await state.update_data(responsible_user_id=responsible_user_id, step="topic")
    topics = await TopicRepository(session).get_all(include_inactive=False)
    options = [(str(t.id), t.topic_name) for t in topics] + [("none", "Без темы")]
    await callback.message.edit_text("Выберите тему:",
                                     reply_markup=creation_choice_keyboard("cr_topic", options))
    await callback.answer()


async def _cr_pick_topic(callback: CallbackQuery, state: FSMContext, k: str | None) -> None:
    data = await state.get_data()
    if data.get("step") != "topic":
        await callback.answer("Некорректный шаг мастера", show_alert=True)
        return
    topic_id = None if k in (None, "none") else int(k)
    await state.update_data(topic_id=topic_id, step="scenario")
    options = [(TaskScenario.SIMPLE, "Простая"), (TaskScenario.ARTICLE_CHECK, "Проверка артикулов")]
    await callback.message.edit_text("Выберите сценарий:",
                                     reply_markup=creation_choice_keyboard("cr_scn", options))
    await callback.answer()


async def _cr_pick_scenario(callback: CallbackQuery, state: FSMContext, k: str | None) -> None:
    data = await state.get_data()
    if data.get("step") != "scenario" or k not in SCENARIO_VALUES:
        await callback.answer("Некорректный шаг мастера", show_alert=True)
        return
    await state.update_data(scenario=k, step="schedule_type")
    options = [(v.value, v.value) for v in (
        ScheduleType.DAILY, ScheduleType.WEEKLY, ScheduleType.MONTHLY,
        ScheduleType.EVERY_N_DAYS, ScheduleType.CRON)]
    await callback.message.edit_text("Выберите тип расписания:",
                                     reply_markup=creation_choice_keyboard("cr_sched", options))
    await callback.answer()


async def _cr_pick_schedule_type(callback: CallbackQuery, state: FSMContext, k: str | None) -> None:
    data = await state.get_data()
    if data.get("step") != "schedule_type" or k not in SCHEDULE_TYPE_VALUES:
        await callback.answer("Некорректный шаг мастера", show_alert=True)
        return
    if k == ScheduleType.DAILY:
        await state.update_data(schedule_type=k, schedule_value=None, schedule_interval=None,
                                step="time")
        await callback.message.edit_text(
            "Введите время создания задачи (ЧЧ:ММ) или «-» для 09:00 по умолчанию:",
            reply_markup=cancel_creation_keyboard())
        await callback.answer()
        return
    hints = {
        ScheduleType.WEEKLY: "Введите день недели (0=понедельник..6=воскресенье):",
        ScheduleType.MONTHLY: "Введите день месяца (1-31):",
        ScheduleType.EVERY_N_DAYS: "Введите интервал в днях (целое число):",
        ScheduleType.CRON: "Введите cron-выражение (например: 0 9 * * *):",
    }
    await state.update_data(schedule_type=k, step="schedule_value")
    await callback.message.edit_text(hints[k], reply_markup=cancel_creation_keyboard())
    await callback.answer()


async def _cr_pick_approval(callback: CallbackQuery, state: FSMContext, k: str | None) -> None:
    data = await state.get_data()
    if data.get("step") != "need_approval" or k not in ("0", "1"):
        await callback.answer("Некорректный шаг мастера", show_alert=True)
        return
    await state.update_data(need_approval=(k == "1"), step="remind_hours")
    await callback.message.edit_text(
        "Через сколько часов напомнить (можно два числа через запятую: первое,второе) "
        "или «-» для значений по умолчанию:", reply_markup=cancel_creation_keyboard())
    await callback.answer()


async def _cr_pick_receiver(callback: CallbackQuery, session, state: FSMContext,
                            actor: User, k: str | None) -> None:
    data = await state.get_data()
    if data.get("step") != "question_receiver":
        await callback.answer("Некорректный шаг мастера", show_alert=True)
        return
    receiver = None if k in (None, "none") else int(k)
    await state.update_data(question_receiver_user_id=receiver)
    final_data = await state.get_data()
    cfg = await _finish_create(session, actor, final_data)
    await session.commit()
    await state.clear()
    text = await render_config_card(session, cfg)
    await callback.message.edit_text(
        text, reply_markup=config_card_keyboard(cfg.id, cfg.is_active))
    await callback.answer("Шаблон создан (неактивен) ✅")


# --------------------------------------------------------------------------
# Точки входа
# --------------------------------------------------------------------------

async def handle_configs_section(callback: CallbackQuery, callback_data: AdminCb, session,
                                 actor: User, svc: AdminService,
                                 state: FSMContext | None = None) -> None:
    """Точка входа для AdminCb.s == "cfg" (пункт меню «✅ Шаблоны задач»).

    Право tasks.manage уже проверено resolve_admin/handle_section до вызова.
    Вся дальнейшая навигация уходит на CfgCb (см. handle_cfg_callback ниже),
    который проверяет право заново."""
    if callback_data.a == "menu":
        await _show_menu(callback, actor, svc)
    else:
        await _show_configs_list(callback, session, 1)


@router.callback_query(CfgCb.filter())
async def handle_cfg_callback(callback: CallbackQuery, callback_data: CfgCb, session,
                              state: FSMContext | None = None, dispatcher=None) -> None:
    actor, svc = await _resolve_actor(callback, session)
    if actor is None:
        return
    action = callback_data.a
    if action in CREATE_ACTIONS and action != "cr_cancel" and state is None:
        await callback.answer("Недоступно", show_alert=True)
        return
    scheduler_svc = _extract_scheduler_svc(dispatcher)
    if action == "list":
        await _show_configs_list(callback, session, callback_data.p)
    elif action == "card":
        await _show_card(callback, session, callback_data.id)
    elif action == "create":
        await _start_create(callback, state)
    elif action == "fields":
        await _show_fields(callback, session, callback_data.id, callback_data.p)
    elif action == "editfield":
        try:
            idx = int(callback_data.k) if callback_data.k is not None else -1
        except ValueError:
            idx = -1
        await _start_edit_field(callback, session, state, actor, svc, scheduler_svc,
                                callback_data.id, idx)
    elif action == "toggle":
        await _toggle_active(callback, session, actor, svc, scheduler_svc, callback_data.id)
    elif action == "clone":
        await _do_clone(callback, session, actor, callback_data.id)
    elif action == "run":
        await _start_manual_run(callback, session, actor, svc, scheduler_svc, callback_data.id)
    elif action == "cr_resp":
        await _cr_pick_responsible(callback, session, state, callback_data.k)
    elif action == "cr_topic":
        await _cr_pick_topic(callback, state, callback_data.k)
    elif action == "cr_scn":
        await _cr_pick_scenario(callback, state, callback_data.k)
    elif action == "cr_sched":
        await _cr_pick_schedule_type(callback, state, callback_data.k)
    elif action == "cr_appr":
        await _cr_pick_approval(callback, state, callback_data.k)
    elif action == "cr_recv":
        await _cr_pick_receiver(callback, session, state, actor, callback_data.k)
    elif action == "cr_cancel":
        await _cancel_create(callback, state)
    elif action == "noop":
        await callback.answer()
    else:
        await callback.answer("Неизвестное действие", show_alert=True)
