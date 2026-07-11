"""Раздел админ-панели «🔄 Синхронизация Google Sheets» (Task 33).

Статус-экран (spreadsheet_id, sync.auto_enabled, время/результат последнего
запуска) + «▶ Dry-run» (безопасно, без confirm_token — ничего не пишет в
бизнес-таблицы, только показывает отчёт) + «✅ Применить» (ОПАСНАЯ операция —
реально меняет данные в БД, только через `AdminService.confirm_token`, как
`_start_reset` в settings.py, Task 24) + toggle `sync.auto_enabled` (без
confirm — тривиально обратимо, тот же принцип, что и bool-переключатели в
Task 30 dictionaries.py) + карточки настроек категории `sync` (переиспользует
`category_keys`/`key_by_index`/`render_setting_card`/`apply_setting_input` из
settings.py, Task 24).

`sync.spreadsheet_id` — ЕДИНСТВЕННЫЙ секретоподобный setting в реестре и
ЕДИНСТВЕННОЕ отклонение от чистого переиспользования settings.py: он
ВСЕГДА маскируется при выводе (статус-экран, список, карточка, prompt
редактирования) через `mask_spreadsheet_id` — брифовое требование. Поэтому
для этого одного ключа список/карточка рендерятся вручную вместо вызова
`render_setting_card` (которая показывает значение как есть).

Как и в разделах Tasks 25-32, навигация использует СОБСТВЕННЫЙ `SynCb`
(prefix="sy"), НЕ `AdminCb` — каждый callback этого модуля и каждое
FSM-продолжение сообщением заново проверяет actor + право `sync.run`
(SECTION_PERMISSIONS["syn"] = "sync.run" — это право покрывает ВЕСЬ раздел,
включая редактирование настроек синхронизации, поэтому переиспользовать
`AdminStates.waiting_value`/`handle_value_message` из settings.py нельзя —
тот хендлер жёстко проверяет `settings.manage`, см. Task 31 lesson).
"""
import json

from aiogram import Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.config import get_settings
from bot.database.repositories.audit_repository import AuditRepository
from bot.handlers.admin.settings import (
    apply_setting_input, category_keys, key_by_index, render_setting_card,
)
from bot.keyboards.admin.confirm import confirm_keyboard
from bot.keyboards.admin.main import AdminCb, admin_menu_keyboard
from bot.keyboards.admin.sync import (
    SynCb, cancel_keyboard, status_back_keyboard, sync_card_keyboard, sync_list_keyboard,
    sync_status_keyboard,
)
from bot.services.admin_service import AdminService
from bot.services.audit_service import AuditService
from bot.services.google_sheets_service import GoogleSheetsService, SheetsClient
from bot.services.setting_service import SETTINGS_REGISTRY, SettingService
from bot.services.user_service import UserService
from bot.states.admin_states import AdminStates
from bot.utils.html_utils import code, html_escape

router = Router(name=__name__)

CATEGORY = "sync"
PERMISSION = "sync.run"


# --------------------------------------------------------------------------
# Доменные функции (тестируются напрямую, без callback-обвязки) — сигнатуры и
# тела заданы брифом Task 33 дословно.
# --------------------------------------------------------------------------

def mask_spreadsheet_id(value: str) -> str:
    if len(value) < 10:
        return "***"
    return f"{value[:4]}…{value[-4:]}"


async def last_sync_summary(session) -> str | None:
    rows = await AuditRepository(session).list_page(page=1, page_size=50)
    for row in rows:
        if row.action == "sync.run":
            return f"{row.created_at:%d.%m.%Y %H:%M} — {row.result}"
    return None


async def dry_run_sync(session, actor) -> dict:
    svc = GoogleSheetsService(session, client=None)
    return await svc.sync_all(dry_run=True, actor_user_id=actor.id)


async def apply_sync(session, actor, client) -> dict:
    svc = GoogleSheetsService(session, client=client)
    return await svc.sync_all(dry_run=False, actor_user_id=actor.id)


async def toggle_auto_sync(session, actor, enabled: bool, scheduler_svc) -> None:
    """Отклонение от буквального кода брифа: коммит вставлен ДО вызова
    `register_sync_job()`. Брифовый код коммитил только у вызывающей стороны,
    ПОСЛЕ возврата из этой функции — но `register_sync_job()` открывает
    СОБСТВЕННУЮ сессию через `session_factory` (Task 12) и на Postgres не
    увидела бы только что записанное, но ещё не закоммиченное значение
    `sync.auto_enabled` (пересобрала бы job по старому значению). Тот же
    класс бага, что и Critical-фикс Task 27, уже устранённый по такой же
    схеме в `apply_schedule_field` (Task 28, `bot/handlers/admin/schedules.py`,
    см. комментарий там же)."""
    settings = SettingService(session)
    await settings.set("sync.auto_enabled", enabled, actor.id)
    await AuditService(session).log(actor.id, "sync.toggle_auto", new_value=enabled)
    await session.commit()
    if scheduler_svc is not None:
        await scheduler_svc.register_sync_job()


# --------------------------------------------------------------------------
# actor-check
# --------------------------------------------------------------------------

async def _resolve_actor(callback: CallbackQuery, session):
    actor = await UserService(session).get_actor(callback.from_user.id)
    svc = AdminService(session)
    if actor is None or not await svc.permissions.has_permission(actor, PERMISSION):
        await callback.answer("Недостаточно прав", show_alert=True)
        return None, None
    return actor, svc


async def _resolve_actor_message(message: Message, session):
    actor = await UserService(session).get_actor(message.from_user.id)
    svc = AdminService(session)
    if actor is None or not await svc.permissions.has_permission(actor, PERMISSION):
        await message.answer("Недостаточно прав")
        return None
    return actor


def _extract_scheduler_svc(dispatcher):
    """Тот же паттерн получения SchedulerService из dispatcher.workflow_data,
    что и в bot/handlers/admin/settings.py, schedules.py, task_configs.py,
    reports.py (Task 24: dp["scheduler"] = scheduler в bot/main.py)."""
    if dispatcher is None:
        return None
    scheduler_obj = getattr(dispatcher, "workflow_data", {}).get("scheduler")
    return getattr(scheduler_obj, "wb_service", None)


# --------------------------------------------------------------------------
# Статус-экран
# --------------------------------------------------------------------------

def _report_text(title: str, results: dict) -> str:
    lines = [title, ""]
    for kind, report in results.items():
        extra = f" (конфликтов: {len(report.skipped_conflicts)})" if report.skipped_conflicts else ""
        lines.append(f"— {kind}: +{report.added} / ~{report.updated} / -{report.deactivated}{extra}")
    return "\n".join(lines)


async def _show_status(callback: CallbackQuery, session) -> None:
    settings_svc = SettingService(session)
    spreadsheet_id = str(await settings_svc.get("sync.spreadsheet_id"))
    masked = mask_spreadsheet_id(spreadsheet_id) if spreadsheet_id else "(не задано)"
    auto_enabled = bool(await settings_svc.get("sync.auto_enabled"))
    summary = await last_sync_summary(session)
    lines = [
        "🔄 Синхронизация Google Sheets", "",
        f"Spreadsheet ID: {code(masked)}",
        f"Автосинхронизация: {'включена ✅' if auto_enabled else 'выключена ❌'}",
        f"Последний запуск: {html_escape(summary) if summary else '—'}",
    ]
    await callback.message.edit_text(
        "\n".join(lines), reply_markup=sync_status_keyboard(auto_enabled))
    await callback.answer()


async def _do_dry_run(callback: CallbackQuery, session, actor) -> None:
    results = await dry_run_sync(session, actor)
    await session.commit()
    text = _report_text("▶ Dry-run — предпросмотр (в БД ничего не изменено)", results)
    await callback.message.edit_text(text, reply_markup=status_back_keyboard())
    await callback.answer()


async def _start_apply(callback: CallbackQuery, session, actor, svc: AdminService) -> None:
    """Реальная синхронизация — опасная операция (реально меняет данные в
    БД), только через confirm_token (см. docstring модуля). `bot`/строковые
    значения захватываются замыканием на момент создания токена, а не
    `session` (Task 27 fix)."""
    settings_svc = SettingService(session)
    spreadsheet_id = (str(await settings_svc.get("sync.spreadsheet_id"))
                      or get_settings().google_sheets_spreadsheet_id)
    creds_file = get_settings().google_sheets_credentials_file

    async def op(session) -> None:
        client = SheetsClient(creds_file, spreadsheet_id)
        await apply_sync(session, actor, client)

    token = svc.confirm_token(
        "sync.apply", op, required_permission=PERMISSION, creator_actor_id=actor.id)
    masked = mask_spreadsheet_id(spreadsheet_id) if spreadsheet_id else "(не задано)"
    await callback.message.edit_text(
        "Применить синхронизацию с Google Sheets прямо сейчас?\n"
        f"Spreadsheet ID: {code(masked)}\n"
        "Это реально изменит данные в БД (пользователи/темы/задачи/артикулы).",
        reply_markup=confirm_keyboard(token))
    await callback.answer()


async def _do_toggle(callback: CallbackQuery, session, actor, dispatcher) -> None:
    current = bool(await SettingService(session).get("sync.auto_enabled"))
    scheduler_svc = _extract_scheduler_svc(dispatcher)
    await toggle_auto_sync(session, actor, not current, scheduler_svc)  # уже коммитит сама
    await _show_status(callback, session)


# --------------------------------------------------------------------------
# Список / карточка / редактирование настроек sync
# --------------------------------------------------------------------------

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
        raw = await settings_svc.get(key)
        if key == "sync.spreadsheet_id":
            value = mask_spreadsheet_id(str(raw)) if raw else "(не задано)"
        else:
            value = json.dumps(raw, ensure_ascii=False)
        entries.append((idx, f"{key} = {value}"[:60]))
    await callback.message.edit_text(
        "⚙ Настройки синхронизации", reply_markup=sync_list_keyboard(entries, page, total_pages))
    await callback.answer()


async def _show_card(callback: CallbackQuery, session, idx: int) -> None:
    try:
        key = key_by_index(CATEGORY, idx)
    except KeyError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    if key == "sync.spreadsheet_id":
        raw = str(await SettingService(session).get(key))
        masked = mask_spreadsheet_id(raw) if raw else "(не задано)"
        d = SETTINGS_REGISTRY[key]
        text = "\n".join([
            f"🔧 {code(key)}",
            f"Описание: {html_escape(d.description or '—')}",
            "Тип: str",
            f"Текущее значение: {code(masked)}",
            "⚠ Значение всегда маскируется в интерфейсе — при редактировании "
            "вводится новое значение полностью.",
        ])
    else:
        text = await render_setting_card(session, key)
    await callback.message.edit_text(text, reply_markup=sync_card_keyboard(idx))
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
    current = await SettingService(session).get(key)
    shown = mask_spreadsheet_id(str(current)) if key == "sync.spreadsheet_id" and current else current
    await state.set_state(AdminStates.waiting_syn_value)
    await state.update_data(setting_key=key, idx=idx)
    await callback.message.edit_text(
        f"Текущее значение {key}: {shown}\nВведите новое значение сообщением:",
        reply_markup=cancel_keyboard(idx))
    await callback.answer()


@router.message(AdminStates.waiting_syn_value)
async def handle_syn_value_message(message: Message, session, state: FSMContext,
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
    scheduler_svc = _extract_scheduler_svc(dispatcher)
    ok, msg = await apply_setting_input(session, actor, key, message.text or "", scheduler_svc)
    await session.commit()
    await message.answer(msg)
    if ok:
        await state.clear()


# --------------------------------------------------------------------------
# Точки входа
# --------------------------------------------------------------------------

async def handle_sync_section(callback: CallbackQuery, callback_data: AdminCb, session,
                              actor, svc: AdminService,
                              state: FSMContext | None = None) -> None:
    """Точка входа для AdminCb.s == "syn" (пункт меню «🔄 Синхронизация
    Google Sheets»). Право sync.run уже проверено resolve_admin/handle_section
    до вызова — дальнейшая навигация уходит на SynCb (handle_syn_callback),
    который проверяет право заново."""
    if callback_data.a == "menu":
        allowed = await svc.visible_sections(actor)
        await callback.message.edit_text("🛠 Админ-панель", reply_markup=admin_menu_keyboard(allowed))
        await callback.answer()
    else:
        await _show_status(callback, session)


@router.callback_query(SynCb.filter())
async def handle_syn_callback(callback: CallbackQuery, callback_data: SynCb, session,
                              state: FSMContext | None = None, dispatcher=None) -> None:
    actor, svc = await _resolve_actor(callback, session)
    if actor is None:
        return
    action = callback_data.a
    if action in ("status", "list", "card") and state is not None:
        await state.clear()
    if action == "status":
        await _show_status(callback, session)
    elif action == "list":
        await _show_list(callback, session, callback_data.p)
    elif action == "card":
        await _show_card(callback, session, callback_data.id)
    elif action == "edit":
        await _start_edit(callback, session, state, callback_data.id)
    elif action == "dryrun":
        await _do_dry_run(callback, session, actor)
    elif action == "apply":
        await _start_apply(callback, session, actor, svc)
    elif action == "toggle":
        await _do_toggle(callback, session, actor, dispatcher)
    elif action == "noop":
        await callback.answer()
    else:
        await callback.answer("Неизвестное действие", show_alert=True)
