"""Раздел админ-панели «⚙ Настройки» (и «🔔 Напоминания и подтверждение»).

Навигация: категории → список настроек категории (пагинация по
general.page_size) → карточка настройки → редактирование (FSM
AdminStates.waiting_value) / сброс к default (опасная операция — только через
AdminService.confirm_token).

Whitelist: в callback_data передаётся ИНДЕКС ключа в отсортированном registry
категории (`k=f"{category}:{idx}"`), не имя поля. `key_by_index` — единственный
способ превратить индекс обратно в ключ, и он проверяет диапазон против
`category_keys()` (который сам берёт ключи только из SETTINGS_REGISTRY).
Записи применяются исключительно через `SettingService.set/reset`, которые
сами проверяют ключ по registry — никакого `setattr` по произвольной строке.
"""
import json

from aiogram import Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.keyboards.admin.confirm import confirm_keyboard
from bot.keyboards.admin.main import AdminCb, admin_menu_keyboard
from bot.keyboards.admin.settings import (
    CATEGORY_TITLES, categories_keyboard, setting_card_keyboard, settings_list_keyboard,
)
from bot.services.admin_service import AdminService
from bot.services.setting_service import SETTINGS_REGISTRY, SettingService
from bot.services.user_service import UserService
from bot.states.admin_states import AdminStates
from bot.utils.html_utils import code, html_escape
from bot.utils.validation import validate_int, validate_json_value, validate_time_str

router = Router(name=__name__)

SCHEDULER_AFFECTING = {"reports.weekday", "reports.time",
                       "sync.auto_enabled", "sync.interval_minutes"}

# Категории раздела "set" (все редактируемые) и "rem" (только напоминания +
# подтверждение — пункт меню "🔔 Напоминания и подтверждение").
ALL_CATEGORIES = sorted({d.category for d in SETTINGS_REGISTRY.values() if d.is_editable})
REM_CATEGORIES = ["reminders", "approval"]


def category_keys(category: str) -> list[str]:
    """Детерминированный порядок ключей категории; в callback уходит индекс."""
    return sorted(k for k, d in SETTINGS_REGISTRY.items()
                  if d.category == category and d.is_editable)


def key_by_index(category: str, index: int) -> str:
    keys = category_keys(category)
    if not 0 <= index < len(keys):
        raise KeyError("Неизвестная настройка")       # whitelist: индекс вне registry
    return keys[index]


async def render_setting_card(session, key: str) -> str:
    d = SETTINGS_REGISTRY[key]
    value = await SettingService(session).get(key)
    type_name = {int: "int", bool: "bool", str: "str"}.get(d.value_type, "json")
    lines = [f"🔧 {code(key)}",
             f"Описание: {html_escape(d.description or '—')}",
             f"Тип: {type_name}",
             f"Текущее значение: {code(json.dumps(value, ensure_ascii=False))}",
             f"Default: {code(json.dumps(d.default, ensure_ascii=False))}"]
    if d.choices:
        lines.append("Допустимо: " + ", ".join(d.choices))
    if d.min_ is not None or d.max_ is not None:
        lines.append(f"Диапазон: {d.min_}..{d.max_}")
    return "\n".join(lines)


def parse_raw(key: str, raw: str) -> object:
    d = SETTINGS_REGISTRY[key]
    if d.value_type is int:
        return validate_int(raw, d.min_, d.max_)
    if d.value_type is bool:
        if raw.strip().lower() in ("да", "вкл", "true", "1"):
            return True
        if raw.strip().lower() in ("нет", "выкл", "false", "0"):
            return False
        raise ValueError("Введите: да/нет")
    if d.value_type is str:
        if key.endswith(".time") or "quiet_hours" in key:
            validate_time_str(raw)                     # формат ЧЧ:ММ
        return raw.strip()
    return validate_json_value(raw)


async def apply_setting_input(session, actor, key: str, raw: str,
                              scheduler_svc) -> tuple[bool, str]:
    svc = SettingService(session)
    try:
        value = parse_raw(key, raw)
        await svc.set(key, value, actor_user_id=actor.id)
    except (ValueError, KeyError, PermissionError) as exc:
        return False, str(exc)
    if key in SCHEDULER_AFFECTING and scheduler_svc is not None:
        if key.startswith("reports."):
            await scheduler_svc.register_report_job()
        else:
            await scheduler_svc.register_sync_job()
    return True, "Сохранено ✅"


# --------------------------------------------------------------------------
# Callback-навигация
# --------------------------------------------------------------------------

def _split_k(k: str) -> tuple[str, int]:
    category, _, idx_s = k.rpartition(":")
    return category, int(idx_s)


async def _show_categories(callback: CallbackQuery, section: str,
                           categories: list[str]) -> None:
    title = ("⚙ Настройки — выберите категорию" if section == "set"
             else "🔔 Напоминания и подтверждение")
    await callback.message.edit_text(title, reply_markup=categories_keyboard(section, categories))
    await callback.answer()


async def _show_menu(callback: CallbackQuery, actor, svc: AdminService) -> None:
    allowed = await svc.visible_sections(actor)
    await callback.message.edit_text("🛠 Админ-панель", reply_markup=admin_menu_keyboard(allowed))
    await callback.answer()


async def _show_settings_list(callback: CallbackQuery, session, category: str, page: int) -> None:
    keys = category_keys(category)
    if not keys:
        await callback.answer("В категории нет настроек", show_alert=True)
        return
    settings_svc = SettingService(session)
    page_size = max(1, int(await settings_svc.get("general.page_size")))
    total_pages = max(1, -(-len(keys) // page_size))
    page = min(max(1, page), total_pages)
    start = (page - 1) * page_size
    entries: list[tuple[int, str, str]] = []
    for offset, key in enumerate(keys[start:start + page_size]):
        idx = start + offset
        value = await settings_svc.get(key)
        label = f"{key} = {json.dumps(value, ensure_ascii=False)}"
        entries.append((idx, key, label[:60]))
    title = CATEGORY_TITLES.get(category, category)
    await callback.message.edit_text(
        f"⚙ {title}", reply_markup=settings_list_keyboard(category, entries, page, total_pages))
    await callback.answer()


async def _show_card(callback: CallbackQuery, session, category: str, idx: int) -> None:
    try:
        key = key_by_index(category, idx)
    except KeyError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    text = await render_setting_card(session, key)
    await callback.message.edit_text(text, reply_markup=setting_card_keyboard(category, idx))
    await callback.answer()


async def _start_edit(callback: CallbackQuery, session, state: FSMContext | None,
                      category: str, idx: int) -> None:
    try:
        key = key_by_index(category, idx)
    except KeyError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    if state is None:                                  # нет FSM-контекста — не тот транспорт
        await callback.answer("Недоступно", show_alert=True)
        return
    current = await SettingService(session).get(key)
    await state.set_state(AdminStates.waiting_value)
    await state.update_data(setting_key=key, category=category, idx=idx)
    await callback.message.edit_text(
        f"Текущее значение {code(key)}: {code(json.dumps(current, ensure_ascii=False))}\n"
        "Введите новое значение сообщением:")
    await callback.answer()


async def _start_reset(callback: CallbackQuery, session, actor, svc: AdminService,
                       category: str, idx: int) -> None:
    """Сброс к default — ОПАСНАЯ операция: выполняется только после подтверждения
    через AdminService.confirm_token с required_permission="settings.manage"
    (см. handle_confirm в bot/handlers/admin/main.py, Task 23 security fix)."""
    try:
        key = key_by_index(category, idx)
    except KeyError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    d = SETTINGS_REGISTRY[key]
    settings_svc = SettingService(session)
    old_value = await settings_svc.get(key)

    async def op() -> None:
        await SettingService(session).reset(key, actor_user_id=actor.id)

    token = svc.confirm_token(
        f"settings.reset.{key}", op,
        required_permission="settings.manage", creator_actor_id=actor.id)
    text = (f"Сбросить {code(key)} к значению по умолчанию?\n"
            f"Текущее: {code(json.dumps(old_value, ensure_ascii=False))}\n"
            f"Default: {code(json.dumps(d.default, ensure_ascii=False))}")
    await callback.message.edit_text(text, reply_markup=confirm_keyboard(token))
    await callback.answer()


async def _dispatch(callback: CallbackQuery, callback_data: AdminCb, session, actor,
                    svc: AdminService, state: FSMContext | None,
                    section: str, root_categories: list[str]) -> None:
    action = callback_data.a
    if action == "open":
        await _show_categories(callback, section, root_categories)
    elif action == "menu":
        await _show_menu(callback, actor, svc)
    elif action == "cat":
        await _show_settings_list(callback, session, callback_data.k, callback_data.p)
    elif action == "card":
        category, idx = _split_k(callback_data.k)
        await _show_card(callback, session, category, idx)
    elif action == "edit":
        category, idx = _split_k(callback_data.k)
        await _start_edit(callback, session, state, category, idx)
    elif action == "reset":
        category, idx = _split_k(callback_data.k)
        await _start_reset(callback, session, actor, svc, category, idx)
    elif action == "noop":
        await callback.answer()
    else:
        await callback.answer("Неизвестное действие", show_alert=True)


async def handle_settings_section(callback: CallbackQuery, callback_data: AdminCb, session,
                                  actor, svc, state: FSMContext | None = None) -> None:
    """Точка входа для AdminCb.s == "set" (пункт меню «⚙ Настройки»).

    Право settings.manage уже проверено `resolve_admin`/`handle_section` до
    вызова этого handler'а — здесь его повторно не проверяем."""
    await _dispatch(callback, callback_data, session, actor, svc, state, "set", ALL_CATEGORIES)


async def handle_reminders_section(callback: CallbackQuery, callback_data: AdminCb, session,
                                   actor, svc, state: FSMContext | None = None) -> None:
    """Точка входа для AdminCb.s == "rem" (пункт меню «🔔 Напоминания и
    подтверждение»). Право проверяется как для "set" — см. SECTION_ALIASES.
    Открывает те же карточки настроек, что и "set", но стартует сразу с
    категорий reminders + approval вместо полного списка."""
    await _dispatch(callback, callback_data, session, actor, svc, state, "rem", REM_CATEGORIES)


# --------------------------------------------------------------------------
# FSM: ввод нового значения (AdminStates.waiting_value)
# --------------------------------------------------------------------------

@router.message(AdminStates.waiting_value)
async def handle_value_message(message: Message, session, state: FSMContext,
                               dispatcher=None) -> None:
    """Отдельная точка входа (обычное текстовое сообщение, а не callback) —
    НЕ проходит через resolve_admin/handle_section, поэтому право settings.manage
    и actor проверяются здесь заново (Tasks 17-23 требование: каждый handler
    сам проверяет actor is None и право до применения изменений)."""
    data = await state.get_data()
    key = data.get("setting_key")
    actor = await UserService(session).get_actor(message.from_user.id)
    admin_svc = AdminService(session)
    if actor is None or key is None or \
            not await admin_svc.permissions.has_permission(actor, "settings.manage"):
        await message.answer("Недостаточно прав")
        await state.clear()
        return
    # scheduler передаётся через dispatcher.workflow_data (dp["scheduler"] = scheduler
    # в bot/main.py; scheduler.wb_service — SchedulerService, см. setup_scheduler).
    scheduler_obj = None
    if dispatcher is not None:
        scheduler_obj = getattr(dispatcher, "workflow_data", {}).get("scheduler")
    scheduler_svc = getattr(scheduler_obj, "wb_service", None)
    ok, msg = await apply_setting_input(session, actor, key, message.text or "", scheduler_svc)
    await session.commit()
    await message.answer(msg)
    if ok:
        await state.clear()                             # ошибка — оставляем в FSM для повтора
