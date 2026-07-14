"""Раздел админ-панели «📊 Отчёты» (Task 32).

Показывает все 12 настроек категории `reports` (реестр `SETTINGS_REGISTRY`,
Task 7), переиспользуя `category_keys`/`key_by_index`/`render_setting_card`/
`apply_setting_input` из `bot/handlers/admin/settings.py` (Task 24) — это
ЧИСТЫЕ функции без встроенной проверки прав, поэтому их можно переиспользовать
под другим правом (`reports.manage`, не `settings.manage`).

Плюс две операции поверх обычных карточек настроек:
- «👁 Предпросмотр» — `preview_report()` вызывает
  `ReportService.build_weekly_report()` и показывает готовый текст админу БЕЗ
  какой-либо отправки (не трогает группу/личку получателей).
- «📤 Сформировать и отправить сейчас» — ОПАСНАЯ операция (реальная рассылка
  в группу и получателям), выполняется только через `AdminService.confirm_token`
  (как `_start_reset` в settings.py, Task 24) — `send_report_now()` вызывается
  из `op(session)`, `bot` захватывается замыканием на момент создания токена
  (Bot — процесс-широкий объект, единственное, что НЕЛЬЗЯ захватывать через
  замыкание — это `session`, см. `AdminService.confirm_token` docstring,
  Task 27 review fix).

Как и в разделах Tasks 25-31, навигация использует СОБСТВЕННЫЙ `RepCb`
(prefix="r"), НЕ `AdminCb` — каждый callback этого модуля и каждое
FSM-продолжение сообщением заново проверяет actor + право `reports.manage`
(нельзя переиспользовать `AdminStates.waiting_value`/`handle_value_message` из
settings.py — тот хендлер жёстко проверяет `settings.manage`, см. Task 31
lesson).
"""
from aiogram import Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.handlers.admin.settings import (
    apply_setting_input, category_keys, format_setting_value, key_by_index,
    render_setting_card,
)
from bot.keyboards.admin.confirm import confirm_keyboard
from bot.keyboards.admin.main import AdminCb, admin_menu_keyboard
from bot.keyboards.admin.reports import (
    RepCb, back_to_list_keyboard, cancel_keyboard, report_setting_card_keyboard,
    reports_list_keyboard,
)
from bot.services.admin_service import AdminService
from bot.services.audit_service import AuditService
from bot.services.report_service import ReportService, weekly_report_job
from bot.services.setting_service import SETTINGS_REGISTRY, SettingService
from bot.services.user_service import UserService
from bot.states.admin_states import AdminStates

router = Router(name=__name__)

CATEGORY = "reports"


# --------------------------------------------------------------------------
# Доменные функции (тестируются напрямую, без callback-обвязки)
# --------------------------------------------------------------------------

async def preview_report(session) -> str:
    return await ReportService(session).build_weekly_report()


async def send_report_now(session, bot, actor) -> None:
    await weekly_report_job(bot, None, session=session)
    await AuditService(session).log(actor.id, "report.manual_send")


# --------------------------------------------------------------------------
# actor-check (каждый RepCb-callback и каждое FSM-сообщение проверяют заново)
# --------------------------------------------------------------------------

async def _resolve_actor(callback: CallbackQuery, session):
    actor = await UserService(session).get_actor(callback.from_user.id)
    svc = AdminService(session)
    if actor is None or not await svc.permissions.has_permission(actor, "reports.manage"):
        await callback.answer("Недостаточно прав", show_alert=True)
        return None, None
    return actor, svc


async def _resolve_actor_message(message: Message, session):
    actor = await UserService(session).get_actor(message.from_user.id)
    svc = AdminService(session)
    if actor is None or not await svc.permissions.has_permission(actor, "reports.manage"):
        await message.answer("Недостаточно прав")
        return None
    return actor


# --------------------------------------------------------------------------
# Список / карточка
# --------------------------------------------------------------------------

async def _show_menu(callback: CallbackQuery, actor, svc: AdminService) -> None:
    allowed = await svc.visible_sections(actor)
    await callback.message.edit_text("🛠 Админ-панель", reply_markup=admin_menu_keyboard(allowed))
    await callback.answer()


async def _show_list(callback: CallbackQuery, session, page: int) -> None:
    keys = category_keys(CATEGORY)
    settings_svc = SettingService(session)
    page_size = max(1, int(await settings_svc.get("general.page_size")))
    total_pages = max(1, -(-len(keys) // page_size))
    page = min(max(1, page), total_pages)
    start = (page - 1) * page_size
    entries: list[tuple[int, str]] = []
    for offset, key in enumerate(keys[start:start + page_size]):
        idx = start + offset
        d = SETTINGS_REGISTRY[key]
        value = await settings_svc.get(key)
        value_display = await format_setting_value(session, d, value)
        entries.append((idx, f"{d.description or key}: {value_display}"[:60]))
    await callback.message.edit_text(
        "📊 Отчёты", reply_markup=reports_list_keyboard(entries, page, total_pages))
    await callback.answer()


async def _show_card(callback: CallbackQuery, session, idx: int) -> None:
    try:
        key = key_by_index(CATEGORY, idx)
    except KeyError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    text = await render_setting_card(session, key)
    await callback.message.edit_text(text, reply_markup=report_setting_card_keyboard(idx))
    await callback.answer()


async def _start_edit(callback: CallbackQuery, session, state: FSMContext | None, idx: int) -> None:
    try:
        key = key_by_index(CATEGORY, idx)
    except KeyError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    if state is None:
        await callback.answer("Недоступно", show_alert=True)
        return
    d = SETTINGS_REGISTRY[key]
    current = await SettingService(session).get(key)
    current_display = await format_setting_value(session, d, current)
    await state.set_state(AdminStates.waiting_rep_value)
    await state.update_data(setting_key=key, idx=idx)
    await callback.message.edit_text(
        f"{d.description or key}\nТекущее значение: {current_display}\n"
        "Введите новое значение сообщением:",
        reply_markup=cancel_keyboard(idx))
    await callback.answer()


@router.message(AdminStates.waiting_rep_value)
async def handle_rep_value_message(message: Message, session, state: FSMContext,
                                   dispatcher=None) -> None:
    actor = await _resolve_actor_message(message, session)
    if actor is None:
        await state.clear()
        return
    data = await state.get_data()
    key = data.get("setting_key")
    if key is None:
        await message.answer("Сессия редактирования утеряна, начните заново")
        await state.clear()
        return
    scheduler_obj = None
    if dispatcher is not None:
        scheduler_obj = getattr(dispatcher, "workflow_data", {}).get("scheduler")
    scheduler_svc = getattr(scheduler_obj, "wb_service", None)
    ok, msg = await apply_setting_input(session, actor, key, message.text or "", scheduler_svc)
    await session.commit()
    await message.answer(msg)
    if ok:
        await state.clear()


# --------------------------------------------------------------------------
# Предпросмотр / отправка сейчас
# --------------------------------------------------------------------------

async def _show_preview(callback: CallbackQuery, session) -> None:
    text = await preview_report(session)
    await callback.message.edit_text(text, reply_markup=back_to_list_keyboard())
    await callback.answer()


async def _start_send(callback: CallbackQuery, session, actor, svc: AdminService) -> None:
    """Реальная рассылка отчёта — опасная операция, только через confirm_token
    (см. docstring модуля). `bot` захватывается замыканием на момент создания
    токена (не `session`, см. AdminService.confirm_token, Task 27 fix)."""
    bot = callback.bot

    async def op(session) -> None:
        await send_report_now(session, bot, actor)

    token = svc.confirm_token(
        "report.manual_send", op, required_permission="reports.manage", creator_actor_id=actor.id)
    await callback.message.edit_text(
        "Сформировать и отправить еженедельный отчёт прямо сейчас "
        "(в группу и всем личным получателям)?",
        reply_markup=confirm_keyboard(token))
    await callback.answer()


# --------------------------------------------------------------------------
# Точки входа
# --------------------------------------------------------------------------

async def handle_reports_section(callback: CallbackQuery, callback_data: AdminCb, session,
                                 actor, svc: AdminService,
                                 state: FSMContext | None = None) -> None:
    """Точка входа для AdminCb.s == "rep" (пункт меню «📊 Отчёты»). Право
    reports.manage уже проверено resolve_admin/handle_section до вызова —
    дальнейшая навигация уходит на RepCb (handle_rep_callback), который
    проверяет право заново."""
    if callback_data.a == "menu":
        await _show_menu(callback, actor, svc)
    else:
        await _show_list(callback, session, 1)


@router.callback_query(RepCb.filter())
async def handle_rep_callback(callback: CallbackQuery, callback_data: RepCb, session,
                              state: FSMContext | None = None) -> None:
    actor, svc = await _resolve_actor(callback, session)
    if actor is None:
        return
    action = callback_data.a
    if action in ("list", "card") and state is not None:
        await state.clear()
    if action == "list":
        await _show_list(callback, session, callback_data.p)
    elif action == "card":
        await _show_card(callback, session, callback_data.id)
    elif action == "edit":
        await _start_edit(callback, session, state, callback_data.id)
    elif action == "preview":
        await _show_preview(callback, session)
    elif action == "send":
        await _start_send(callback, session, actor, svc)
    elif action == "noop":
        await callback.answer()
    else:
        await callback.answer("Неизвестное действие", show_alert=True)
