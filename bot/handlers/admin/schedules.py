"""Раздел админ-панели «📅 Расписания» (Task 28).

Отдельный раздел от «✅ Шаблоны задач» (Task 27), несмотря на то, что оба
редактируют поля того же `TaskConfig`: право `schedules.manage` (этот раздел)
намеренно отделено от `tasks.manage` (Task 27) в реестре `SECTION_PERMISSIONS`
(Task 23) — так владелец может выдать сотруднику возможность перенастраивать
расписание запуска задач, не давая полный доступ к CRUD шаблонов (название,
ответственный, подтверждение и т.д.).

Навигация: список АКТИВНЫХ шаблонов (только у активных есть смысл менять
расписание — job планировщика существует лишь для них) -> карточка расписания
(`schedule_type/value/interval/time/due_time/run_on_weekends/skip_holidays/
next_run_at`) -> «✏ Изменить поле расписания» (whitelist по индексу в
`SCHEDULE_FIELD_LIST`, тот же паттерн, что `fields_list_keyboard` Task 27) ->
FSM текстового ввода / мгновенный toggle bool-полей / выбор кнопкой
(`schedule_type`, день недели для `weekly`). После КАЖДОГО сохранения —
`SchedulerService.rebuild_config_job(config_id)` (идемпотентно, `remove_job`
перед `add_job`, см. Task 12/26/27).

Как и в разделах Tasks 25-27, навигация использует СОБСТВЕННЫЙ `SchCb`
(prefix="sc"), НЕ `AdminCb` — каждый callback этого модуля и каждое
FSM-продолжение сообщением заново проверяет actor + право `schedules.manage`
(`resolve_admin` срабатывает только для входа в раздел через `AdminCb`).

Два осознанных отклонения от буквального кода брифа Task 28 (оба —
документированные фиксы того же класса дефектов, что уже находились и
исправлялись в Tasks 24-27, а не новые предположения):

1. `_parse` брифа НЕ валидирует `schedule_type` против `ScheduleType` (кнопка
   "type что угодно" молча прошла бы) и не парсит `first_run_date` в `date`
   (осталась бы Python-строка в Date-колонке). Здесь `schedule_type`
   редактируется ТОЛЬКО кнопкой (закрытый список `ScheduleType`), `schedule_value`
   при `weekly`/`monthly` валидируется диапазоном 0..6/1..31 (день недели —
   кнопкой, день месяца — числом с проверкой), `first_run_date` парсится через
   `_parse_date` (переиспользован из `task_configs.py`, та же логика, что уже
   работает в мастере создания шаблона).
2. Брифовый `apply_schedule_field` вызывает `scheduler_svc.rebuild_config_job(...)`
   ДО коммита (коммит — на совести вызывающего теста/handler'а, что видно из
   данного брифом теста `test_apply_schedule_field_rebuilds_without_duplicate`:
   `apply_schedule_field(...)` -> `await s.commit()`). Это ровно системный баг
   Task 27 review (Critical): `rebuild_config_job` открывает СВОЮ сессию и на
   Postgres не увидит незакоммиченное поле. Здесь `apply_schedule_field` коммитит
   ВНУТРИ себя перед rebuild — повторный `await s.commit()` в тесте становится
   безопасным no-op (нечего коммитить).
"""
from datetime import date

from aiogram import Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from apscheduler.triggers.cron import CronTrigger

from bot.database.models import ScheduleType, TaskConfig, User
from bot.database.repositories.task_repository import TaskRepository
from bot.handlers.admin.task_configs import _parse_date
from bot.keyboards.admin.main import AdminCb, admin_menu_keyboard
from bot.keyboards.admin.schedules import (
    SchCb, cancel_edit_keyboard, schedule_card_keyboard, schedule_fields_keyboard,
    schedule_type_choice_keyboard, schedules_list_keyboard, weekday_choice_keyboard,
)
from bot.services.admin_service import AdminService
from bot.services.audit_service import AuditService
from bot.services.setting_service import SettingService
from bot.services.user_service import UserService
from bot.states.admin_states import AdminStates
from bot.utils.html_utils import html_escape
from bot.utils.validation import validate_int, validate_time_str

router = Router(name=__name__)

# --------------------------------------------------------------------------
# Доменные функции (тестируются напрямую, без callback-обвязки)
# --------------------------------------------------------------------------

SCHEDULE_FIELDS: frozenset[str] = frozenset({
    "schedule_type", "schedule_value", "schedule_interval", "time", "due_time",
    "due_days_offset", "first_run_date", "run_on_weekends", "skip_holidays",
})

# Порядок для whitelist-редактирования по индексу (не по имени поля из callback).
# due_days_offset добавлено СТРОГО В КОНЕЦ — вставка в середину сдвинула бы
# индексы существующих полей.
SCHEDULE_FIELD_LIST: list[str] = [
    "schedule_type", "schedule_value", "schedule_interval", "time", "due_time",
    "first_run_date", "run_on_weekends", "skip_holidays", "due_days_offset",
]

FIELD_TITLES: dict[str, str] = {
    "schedule_type": "Тип расписания",
    "schedule_value": "Значение расписания",
    "schedule_interval": "Интервал (дней)",
    "time": "Время создания",
    "due_time": "Срок (due_time)",
    "due_days_offset": "Срок: дней после отправки",
    "first_run_date": "Дата первого запуска",
    "run_on_weekends": "Запуск в выходные",
    "skip_holidays": "Пропуск праздников",
}

BOOL_FIELDS: frozenset[str] = frozenset({"run_on_weekends", "skip_holidays"})
SCHEDULE_TYPE_VALUES: frozenset[str] = frozenset(s.value for s in ScheduleType)


def _parse(field: str, raw: str, schedule_type: str | None) -> object:
    if field == "schedule_type":
        value = raw.strip()
        if value not in SCHEDULE_TYPE_VALUES:
            raise ValueError("Допустимые типы: " + ", ".join(sorted(SCHEDULE_TYPE_VALUES)))
        return value
    if field == "schedule_interval":
        return validate_int(raw, 1, 365)
    if field == "due_days_offset":
        return validate_int(raw, 0, 30)                 # 0 — дедлайн в день отправки
    if field in ("time", "due_time"):
        return validate_time_str(raw)
    if field == "first_run_date":
        return _parse_date(raw)
    if field == "schedule_value":
        if schedule_type == ScheduleType.CRON:
            try:
                CronTrigger.from_crontab(raw)
            except ValueError:
                raise ValueError("Некорректное cron-выражение") from None
            return raw
        if schedule_type == ScheduleType.WEEKLY:
            return str(validate_int(raw, 0, 6))
        if schedule_type == ScheduleType.MONTHLY:
            return str(validate_int(raw, 1, 31))
        return raw.strip() or None
    if field in ("run_on_weekends", "skip_holidays"):
        return raw.strip().lower() in ("да", "1", "true")
    return raw.strip()


async def apply_schedule_field(session, actor: User, config_id: int, field: str,
                               raw: str, scheduler_svc) -> tuple[bool, str]:
    if field not in SCHEDULE_FIELDS:
        return False, "Поле недоступно для редактирования расписания"
    repo = TaskRepository(session)
    cfg = await repo.get_config(config_id)
    if cfg is None:
        return False, "Шаблон не найден"
    try:
        value = _parse(field, raw, cfg.schedule_type)
    except ValueError as exc:
        return False, str(exc)
    old = getattr(cfg, field)
    setattr(cfg, field, value)             # безопасно: field уже прошёл whitelist SCHEDULE_FIELDS
    await session.flush()
    await AuditService(session).log(actor.id, "schedule.edit",
                                    entity_type="task_config", entity_id=str(config_id),
                                    setting_key=field, old_value=str(old), new_value=str(value))
    await session.commit()                 # коммит ДО rebuild — rebuild_config_job открывает
                                            # СВОЮ сессию и на Postgres не увидит незакоммиченное
                                            # поле (Task 27 review fix, тот же класс бага).
    if scheduler_svc is not None:
        await scheduler_svc.rebuild_config_job(config_id)
    return True, "Расписание обновлено ✅"


# --------------------------------------------------------------------------
# Отображение
# --------------------------------------------------------------------------

def render_schedule_card(cfg: TaskConfig) -> str:
    lines = [
        f"📅 {html_escape(cfg.title)}",
        f"Тип расписания: {html_escape(cfg.schedule_type)}",
        f"Значение расписания: {html_escape(cfg.schedule_value) if cfg.schedule_value else '—'}",
        f"Интервал (дней): {cfg.schedule_interval if cfg.schedule_interval is not None else '—'}",
        f"Время: {cfg.time.strftime('%H:%M') if cfg.time else '—'}",
        f"Срок (due_time): {cfg.due_time.strftime('%H:%M') if cfg.due_time else '—'}",
        f"Срок: дней после отправки: {cfg.due_days_offset}",
        f"Дата первого запуска: {cfg.first_run_date.isoformat() if cfg.first_run_date else '—'}",
        f"Запуск в выходные: {'да' if cfg.run_on_weekends else 'нет'}",
        f"Пропуск праздников: {'да' if cfg.skip_holidays else 'нет'}",
        f"Ближайший запуск: {cfg.next_run_at.strftime('%d.%m.%Y %H:%M') if cfg.next_run_at else '—'}",
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------
# actor-check (каждый SchCb-callback и каждое FSM-сообщение проверяют заново —
# resolve_admin из main.py срабатывает только для AdminCb, не для SchCb)
# --------------------------------------------------------------------------

async def _resolve_actor(callback: CallbackQuery, session) -> tuple[User | None, AdminService | None]:
    actor = await UserService(session).get_actor(callback.from_user.id)
    svc = AdminService(session)
    if actor is None or not await svc.permissions.has_permission(actor, "schedules.manage"):
        await callback.answer("Недостаточно прав", show_alert=True)
        return None, None
    return actor, svc


async def _resolve_actor_message(message: Message, session) -> User | None:
    actor = await UserService(session).get_actor(message.from_user.id)
    svc = AdminService(session)
    if actor is None or not await svc.permissions.has_permission(actor, "schedules.manage"):
        await message.answer("Недостаточно прав")
        return None
    return actor


def _extract_scheduler_svc(dispatcher):
    """Тот же паттерн получения SchedulerService из dispatcher.workflow_data,
    что и bot/handlers/admin/settings.py и bot/handlers/admin/task_configs.py
    (Task 24: dp["scheduler"] = scheduler в bot/main.py)."""
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


async def _show_schedules_list(callback: CallbackQuery, session, page: int) -> None:
    configs = sorted(await TaskRepository(session).get_active_configs(), key=lambda c: c.title)
    settings_svc = SettingService(session)
    page_size = max(1, int(await settings_svc.get("general.page_size")))
    total_pages = max(1, -(-len(configs) // page_size))
    page = min(max(1, page), total_pages)
    start = (page - 1) * page_size
    entries = [(c.id, f"{c.title} ({c.schedule_type})") for c in configs[start:start + page_size]]
    await callback.message.edit_text(
        "📅 Расписания", reply_markup=schedules_list_keyboard(entries, page, total_pages))
    await callback.answer()


async def _show_card(callback: CallbackQuery, session, config_id: int) -> None:
    cfg = await TaskRepository(session).get_config(config_id)
    if cfg is None:
        await callback.answer("Шаблон не найден", show_alert=True)
        return
    await callback.message.edit_text(
        render_schedule_card(cfg), reply_markup=schedule_card_keyboard(cfg.id))
    await callback.answer()


async def _show_fields(callback: CallbackQuery, session, config_id: int, page: int) -> None:
    cfg = await TaskRepository(session).get_config(config_id)
    if cfg is None:
        await callback.answer("Шаблон не найден", show_alert=True)
        return
    settings_svc = SettingService(session)
    page_size = max(1, int(await settings_svc.get("general.page_size")))
    total_pages = max(1, -(-len(SCHEDULE_FIELD_LIST) // page_size))
    page = min(max(1, page), total_pages)
    start = (page - 1) * page_size
    entries: list[tuple[int, str]] = []
    for offset, field in enumerate(SCHEDULE_FIELD_LIST[start:start + page_size]):
        idx = start + offset
        value = getattr(cfg, field)
        if field in BOOL_FIELDS:
            label = f"{'✅' if value else '▫'} {FIELD_TITLES[field]}"
        else:
            label = f"{FIELD_TITLES[field]} = {value if value is not None else '—'}"
        entries.append((idx, label[:60]))
    await callback.message.edit_text(
        "✏ Изменить поле расписания",
        reply_markup=schedule_fields_keyboard(config_id, entries, page, total_pages))
    await callback.answer()


# --------------------------------------------------------------------------
# Редактирование одного поля (по индексу в SCHEDULE_FIELD_LIST)
# --------------------------------------------------------------------------

async def _start_edit_field(callback: CallbackQuery, session, state: FSMContext | None,
                            actor: User, scheduler_svc, config_id: int, idx: int) -> None:
    if not 0 <= idx < len(SCHEDULE_FIELD_LIST):
        await callback.answer("Неизвестное поле", show_alert=True)
        return
    field = SCHEDULE_FIELD_LIST[idx]
    cfg = await TaskRepository(session).get_config(config_id)
    if cfg is None:
        await callback.answer("Шаблон не найден", show_alert=True)
        return

    if field in BOOL_FIELDS:
        old = getattr(cfg, field)
        ok, msg = await apply_schedule_field(
            session, actor, config_id, field, "0" if old else "1", scheduler_svc)
        if not ok:
            await callback.answer(msg, show_alert=True)
            return
        await _show_fields(callback, session, config_id, 1)
        return

    if field == "schedule_type":
        await callback.message.edit_text(
            "Выберите тип расписания:",
            reply_markup=schedule_type_choice_keyboard(config_id, sorted(SCHEDULE_TYPE_VALUES)))
        await callback.answer()
        return

    if field == "schedule_value" and cfg.schedule_type == ScheduleType.WEEKLY:
        await callback.message.edit_text(
            "Выберите день недели:", reply_markup=weekday_choice_keyboard(config_id))
        await callback.answer()
        return

    if state is None:
        await callback.answer("Недоступно", show_alert=True)
        return
    current = getattr(cfg, field)
    await state.set_state(AdminStates.waiting_sch_edit)
    await state.update_data(config_id=config_id, field=field)
    hint = _hint_for(field, cfg.schedule_type)
    await callback.message.edit_text(
        f"Поле: {html_escape(FIELD_TITLES[field])}\n"
        f"Текущее значение: {current if current is not None else '—'}\n{hint}",
        reply_markup=cancel_edit_keyboard(config_id))
    await callback.answer()


def _hint_for(field: str, schedule_type: str | None) -> str:
    if field == "schedule_interval":
        return "Введите интервал в днях (целое число, 1-365):"
    if field in ("time", "due_time"):
        return "Введите время в формате ЧЧ:ММ:"
    if field == "due_days_offset":
        return "Введите число дней после дня отправки (0-30, 0 — тот же день):"
    if field == "first_run_date":
        return "Введите дату в формате ГГГГ-ММ-ДД:"
    if field == "schedule_value":
        if schedule_type == ScheduleType.CRON:
            return "Введите cron-выражение (например: 0 9 * * *):"
        if schedule_type == ScheduleType.MONTHLY:
            return "Введите день месяца (число 1-31):"
        return "Введите значение расписания:"
    return "Введите новое значение:"


@router.message(AdminStates.waiting_sch_edit)
async def handle_sch_edit_message(message: Message, session, state: FSMContext,
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
    scheduler_svc = _extract_scheduler_svc(dispatcher)
    ok, msg = await apply_schedule_field(
        session, actor, config_id, field, message.text or "", scheduler_svc)
    await message.answer(msg)
    if ok:
        await state.clear()


async def _pick_schedule_type(callback: CallbackQuery, session, actor: User,
                              scheduler_svc, config_id: int, value: str | None) -> None:
    if value is None:
        await callback.answer("Некорректный выбор", show_alert=True)
        return
    ok, msg = await apply_schedule_field(
        session, actor, config_id, "schedule_type", value, scheduler_svc)
    if not ok:
        await callback.answer(msg, show_alert=True)
        return
    await _show_card(callback, session, config_id)


async def _pick_weekday(callback: CallbackQuery, session, actor: User,
                        scheduler_svc, config_id: int, value: str | None) -> None:
    if value is None:
        await callback.answer("Некорректный выбор", show_alert=True)
        return
    ok, msg = await apply_schedule_field(
        session, actor, config_id, "schedule_value", value, scheduler_svc)
    if not ok:
        await callback.answer(msg, show_alert=True)
        return
    await _show_card(callback, session, config_id)


# --------------------------------------------------------------------------
# Точки входа
# --------------------------------------------------------------------------

async def handle_schedules_section(callback: CallbackQuery, callback_data: AdminCb, session,
                                   actor: User, svc: AdminService,
                                   state: FSMContext | None = None) -> None:
    """Точка входа для AdminCb.s == "sch" (пункт меню «📅 Расписания»).

    Право schedules.manage уже проверено resolve_admin/handle_section до
    вызова. Вся дальнейшая навигация уходит на SchCb (см. handle_sch_callback
    ниже), который проверяет право заново."""
    if callback_data.a == "menu":
        await _show_menu(callback, actor, svc)
    else:
        await _show_schedules_list(callback, session, 1)


@router.callback_query(SchCb.filter())
async def handle_sch_callback(callback: CallbackQuery, callback_data: SchCb, session,
                              state: FSMContext | None = None, dispatcher=None) -> None:
    actor, _svc = await _resolve_actor(callback, session)
    if actor is None:
        return
    scheduler_svc = _extract_scheduler_svc(dispatcher)
    action = callback_data.a
    # Навигационные действия (список/карточка/список полей, включая «❌ Отменить»
    # -> SchCb(a="card", ...)) покидают контекст редактирования ОДНОГО поля —
    # обязаны сбросить waiting_sch_edit, иначе следующее произвольное текстовое
    # сообщение пользователя (он думает, что отменил) молча перехватывается
    # handle_sch_edit_message и применяется как новое значение поля расписания
    # (найдено ревью Task 28, воспроизведено эмпирически: клик "Отменить" ->
    # текст "42" -> schedule_interval молча становится 42).
    if action in ("list", "card", "fields") and state is not None:
        await state.clear()
    if action == "list":
        await _show_schedules_list(callback, session, callback_data.p)
    elif action == "card":
        await _show_card(callback, session, callback_data.id)
    elif action == "fields":
        await _show_fields(callback, session, callback_data.id, callback_data.p)
    elif action == "editfield":
        try:
            idx = int(callback_data.k) if callback_data.k is not None else -1
        except ValueError:
            idx = -1
        await _start_edit_field(callback, session, state, actor, scheduler_svc,
                                callback_data.id, idx)
    elif action == "settype":
        await _pick_schedule_type(callback, session, actor, scheduler_svc,
                                  callback_data.id, callback_data.k)
    elif action == "setweekday":
        await _pick_weekday(callback, session, actor, scheduler_svc,
                            callback_data.id, callback_data.k)
    elif action == "noop":
        await callback.answer()
    else:
        await callback.answer("Неизвестное действие", show_alert=True)
