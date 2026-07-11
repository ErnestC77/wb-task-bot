"""Раздел админ-панели «🧾 Журнал изменений» (read-only) + «💾 Резервные
операции» (Task 35).

Оба раздела живут в ОДНОМ файле (как задано брифом Files: только этот модуль
создаётся), но у них РАЗНЫЕ права и НИКАК не связанная навигация:
- «aud» (журнал) — право `audit.view`, ПОЛНОСТЬЮ read-only. В этом модуле
  НЕТ и не должно появиться ни одного handler-а, изменяющего/удаляющего
  запись `AdminAuditLog` — это проверяется регресс-тестом на исходный текст
  модуля (`test_audit_module_has_no_mutation_handlers`, брифовый тест 29).
  Собственный `AudCb` (prefix="au"): пагинация + переключатель «Только мои
  действия»/«Все».
- «bak» (резервные операции, `SECTION_ALIASES["bak"] = "ops"` — право
  `tasks.run_manual`, ТО ЖЕ самое, что у «run»/«act» из Task 34) — текстовая
  инструкция по pg_dump (реальный дамп делается вне Telegram, бот НЕ хранит
  бинарные бэкапы) + две уже реализованные в Task 34 операции восстановления
  (`recover_scheduler`/`recover_pending_deliveries`, импортируются из
  `bot.handlers.admin.operations`, а не дублируются) — обе за confirm_token,
  т.к. реально меняют состояние планировщика/шлют сообщения. Собственный
  `BakCb` (prefix="bk") с независимой проверкой `tasks.run_manual` (то же
  правило, что и во всех разделах с Task 25: своя CallbackData — свой
  повторный actor-check на каждый callback, main.py:resolve_admin отвечает
  только за вход В раздел, не за дальнейшую навигацию внутри).

Отклонение от брифового Step 3 (полная переписка `bot/handlers/admin/__init__.py`
на цикл `for module in (...)` + `SECTION_HANDLERS`, наполняемый "декораторами
в модулях разделов"): на момент Task 35 в репозитории УЖЕ есть явная, ранее
одобренная схема регистрации (Tasks 23-34) — `admin_router.include_router(...)`
+ прямые присваивания `SECTION_HANDLERS["x"] = module.handle_x_section` для
КАЖДОГО раздела, включая разделы с несколькими алиасами на один handler
("dic"/"dic2", "set"/"rem", "run"/"act"). Брифовый Step 3, судя по всему,
писался как generic-заготовка до того, как эта многоалиасная специфика была
реализована (аналогично уже задокументированному в Task 27 несоответствию
про manual_run_config) — общий цикл `hasattr(module, "router")` не знает,
что "aud"/"bak" — РАЗНЫЕ права одного модуля, и полностью потерял бы
дифференциацию алиасов у уже одобренных разделов. Вместо переписывания файла
здесь просто ДОБАВЛЕНЫ явные записи "aud"/"bak" по уже установленному
паттерну — без изменения структуры файла.
"""
from aiogram import Router
from aiogram.types import CallbackQuery

from bot.database.repositories.audit_repository import AuditRepository
from bot.keyboards.admin.audit import AudCb, BakCb, audit_page_keyboard, backup_keyboard
from bot.keyboards.admin.confirm import confirm_keyboard
from bot.keyboards.admin.main import AdminCb, admin_menu_keyboard
from bot.services.admin_service import AdminService
from bot.services.setting_service import SettingService
from bot.services.user_service import UserService
from bot.utils.html_utils import code, html_escape

router = Router(name=__name__)

AUDIT_PERMISSION = "audit.view"
BACKUP_PERMISSION = "tasks.run_manual"

PG_DUMP_HINT = (
    "💾 <b>Резервное копирование</b>\n\n"
    "Бот НЕ хранит и не создаёт бинарные бэкапы внутри Telegram. "
    "Регулярный дамп PostgreSQL выполняется вне бота, например:\n\n"
    "<code>pg_dump -Fc -h HOST -U USER -d DBNAME -f backup_$(date +%Y%m%d).dump</code>\n\n"
    "Восстановление:\n"
    "<code>pg_restore -h HOST -U USER -d DBNAME --clean backup_YYYYMMDD.dump</code>\n\n"
    "Ниже — операции восстановления РАБОТАЮЩЕГО процесса (не файлов БД):"
)


# --------------------------------------------------------------------------
# render_audit_page — буквально по брифу
# --------------------------------------------------------------------------

async def render_audit_page(session, page: int, page_size: int,
                            actor_user_id: int | None = None) -> str:
    rows = await AuditRepository(session).list_page(page=page, page_size=page_size,
                                                     actor_user_id=actor_user_id)
    if not rows:
        return "Журнал пуст"
    lines = ["🧾 <b>Журнал изменений</b>", ""]
    for row in rows:
        lines.append(
            f"#{row.id} {row.created_at:%d.%m %H:%M} "
            f"user={row.actor_user_id or 'system'} {code(row.action)} "
            f"{html_escape(row.setting_key or row.entity_type or '')} "
            f"{html_escape((row.new_value_json or '')[:60])} [{row.result}]")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# actor-check
# --------------------------------------------------------------------------

async def _resolve_actor(callback: CallbackQuery, session, permission: str):
    actor = await UserService(session).get_actor(callback.from_user.id)
    svc = AdminService(session)
    if actor is None or not await svc.permissions.has_permission(actor, permission):
        await callback.answer("Недостаточно прав", show_alert=True)
        return None, None
    return actor, svc


# --------------------------------------------------------------------------
# «🧾 Журнал изменений» (read-only)
# --------------------------------------------------------------------------

async def _show_audit_page(callback: CallbackQuery, session, page: int,
                           mine_only: bool, actor_id: int) -> None:
    settings_svc = SettingService(session)
    page_size = max(1, int(await settings_svc.get("general.page_size")))
    filter_actor_id = actor_id if mine_only else None
    text = await render_audit_page(session, max(1, page), page_size, filter_actor_id)
    await callback.message.edit_text(
        text, reply_markup=audit_page_keyboard(max(1, page), mine_only))
    await callback.answer()


async def handle_audit_section(callback: CallbackQuery, callback_data: AdminCb, session,
                               actor, svc: AdminService, state=None) -> None:
    """Точка входа для AdminCb.s == "aud" (пункт меню «🧾 Журнал изменений»).
    Право audit.view уже проверено resolve_admin/handle_section — дальнейшая
    навигация уходит на AudCb (handle_aud_callback), который проверяет право
    заново."""
    if callback_data.a == "menu":
        allowed = await svc.visible_sections(actor)
        await callback.message.edit_text("🛠 Админ-панель", reply_markup=admin_menu_keyboard(allowed))
        await callback.answer()
        return
    await _show_audit_page(callback, session, 1, False, actor.id)


@router.callback_query(AudCb.filter())
async def handle_aud_callback(callback: CallbackQuery, callback_data: AudCb, session,
                              state=None) -> None:
    actor, svc = await _resolve_actor(callback, session, AUDIT_PERMISSION)
    if actor is None:
        return
    if callback_data.a == "page":
        await _show_audit_page(callback, session, callback_data.p, bool(callback_data.mine), actor.id)
    elif callback_data.a == "toggle":
        await _show_audit_page(callback, session, 1, not bool(callback_data.mine), actor.id)
    elif callback_data.a == "noop":
        await callback.answer()
    else:
        await callback.answer("Неизвестное действие", show_alert=True)


# --------------------------------------------------------------------------
# «💾 Резервные операции»
# --------------------------------------------------------------------------

def _extract_scheduler_and_factory(dispatcher):
    """Тот же паттерн получения объектов планировщика из dispatcher.workflow_data,
    что и в settings.py/schedules.py/task_configs.py/sync.py (Task 24: dp["scheduler"]
    = scheduler в bot/main.py). Здесь нужен и «сырой» AsyncIOScheduler (первый
    позиционный аргумент recover_scheduler), и session_factory с него же."""
    if dispatcher is None:
        return None, None
    scheduler_obj = getattr(dispatcher, "workflow_data", {}).get("scheduler")
    wb_service = getattr(scheduler_obj, "wb_service", None)
    session_factory = getattr(wb_service, "session_factory", None)
    return scheduler_obj, session_factory


async def handle_backup_section(callback: CallbackQuery, callback_data: AdminCb, session,
                                actor, svc: AdminService, state=None) -> None:
    """Точка входа для AdminCb.s == "bak" (пункт меню «💾 Резервные операции»).
    Право tasks.run_manual уже проверено resolve_admin/handle_section (тот же
    alias, что у "run"/"act" из Task 34, см. SECTION_ALIASES) — дальнейшая
    навигация уходит на BakCb (handle_bak_callback), который проверяет право
    заново."""
    if callback_data.a == "menu":
        allowed = await svc.visible_sections(actor)
        await callback.message.edit_text("🛠 Админ-панель", reply_markup=admin_menu_keyboard(allowed))
        await callback.answer()
        return
    await callback.message.edit_text(PG_DUMP_HINT, reply_markup=backup_keyboard())
    await callback.answer()


async def _start_recover_scheduler(callback: CallbackQuery, session, actor, svc: AdminService,
                                   dispatcher) -> None:
    from bot.handlers.admin.operations import recover_scheduler as _recover_scheduler

    scheduler_obj, session_factory = _extract_scheduler_and_factory(dispatcher)
    if scheduler_obj is None or session_factory is None:
        await callback.answer("Планировщик недоступен", show_alert=True)
        return
    bot = callback.bot

    async def op(session) -> dict:
        return await _recover_scheduler(scheduler_obj, bot, session_factory, actor.id)

    token = svc.confirm_token("scheduler.manual_recover", op,
                              required_permission=BACKUP_PERMISSION, creator_actor_id=actor.id)
    await callback.message.edit_text(
        "Вручную восстановить job'ы планировщика (напоминания, эскалации, отчёты, "
        "синхронизацию) из текущего состояния БД?",
        reply_markup=confirm_keyboard(token))
    await callback.answer()


async def _start_recover_deliveries(callback: CallbackQuery, session, actor,
                                    svc: AdminService) -> None:
    from bot.handlers.admin.operations import recover_pending_deliveries
    bot = callback.bot

    async def op(session) -> int:
        return await recover_pending_deliveries(session, bot, actor)

    token = svc.confirm_token("delivery.recover", op,
                              required_permission=BACKUP_PERMISSION, creator_actor_id=actor.id)
    await callback.message.edit_text(
        "Повторно отправить все зависшие (pending/failed/retrying) сообщения задач?",
        reply_markup=confirm_keyboard(token))
    await callback.answer()


@router.callback_query(BakCb.filter())
async def handle_bak_callback(callback: CallbackQuery, callback_data: BakCb, session,
                              state=None, dispatcher=None) -> None:
    actor, svc = await _resolve_actor(callback, session, BACKUP_PERMISSION)
    if actor is None:
        return
    if callback_data.a == "recoverjobs":
        await _start_recover_scheduler(callback, session, actor, svc, dispatcher)
    elif callback_data.a == "recoverdeliv":
        await _start_recover_deliveries(callback, session, actor, svc)
    else:
        await callback.answer("Неизвестное действие", show_alert=True)
