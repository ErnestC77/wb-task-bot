from aiogram import Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.keyboards.admin.confirm import ConfirmCb
from bot.keyboards.admin.main import SECTION_ALIASES, AdminCb, admin_menu_keyboard
from bot.services.admin_service import SECTION_PERMISSIONS, AdminService
from bot.services.user_service import UserService
from bot.utils.logger import get_logger

router = Router(name=__name__)
logger = get_logger(__name__)


@router.message(Command("admin"))
async def cmd_admin(message: Message, session):
    """/admin доступна и в личке, и в группе, но само меню (и всё, что из него
    растёт — подтверждения опасных операций) должно уходить только в ЛС, а не
    утекать в групповой чат, откуда команда могла быть вызвана."""
    actor = await UserService(session).get_actor(message.from_user.id)
    svc = AdminService(session)
    allowed = await svc.visible_sections(actor) if actor else set()
    if not allowed:
        await message.answer("Доступ запрещен")
        return
    try:
        await message.bot.send_message(
            chat_id=actor.telegram_id, text="🛠 Админ-панель",
            reply_markup=admin_menu_keyboard(allowed))
    except Exception as exc:                              # noqa: BLE001
        logger.warning("Admin menu DM failed for %s: %s", actor.telegram_id, exc)
        if message.chat.type != "private":
            await message.answer(
                "Напишите боту в личные сообщения, чтобы открыть админ-панель")
        return
    if message.chat.type != "private":
        await message.answer("Меню отправлено вам в личные сообщения")


async def resolve_admin(callback: CallbackQuery, session, section: str):
    """Единая проверка прав для всех admin-callback (в т.ч. поддельных/чужих)."""
    actor = await UserService(session).get_actor(callback.from_user.id)
    svc = AdminService(session)
    permission = SECTION_PERMISSIONS.get(SECTION_ALIASES.get(section, section))
    if actor is None or permission is None or \
            not await svc.permissions.has_permission(actor, permission):
        await callback.answer("Недостаточно прав", show_alert=True)
        return None, None
    return actor, svc


@router.callback_query(AdminCb.filter())
async def handle_section(callback: CallbackQuery, callback_data: AdminCb, session,
                         state: FSMContext | None = None):
    actor, svc = await resolve_admin(callback, session, callback_data.s)
    if actor is None:
        return
    # диспетчеризация по разделам — подключается в Tasks 24-27
    from bot.handlers.admin import SECTION_HANDLERS
    handler = SECTION_HANDLERS.get(callback_data.s)
    if handler is None:
        await callback.answer("Раздел в разработке")
        return
    # `state` (FSMContext) прокинут сюда для разделов, начинающих ввод значения
    # через AdminStates.waiting_value (Task 24: раздел "Настройки" — редактирование
    # значения по индексу в registry). Раньше (Task 23) сигнатура ограничивалась
    # (callback, callback_data, session, actor, svc); теперь `state` — 6-й
    # параметр, опциональный, чтобы не ломать существующие вызовы/тесты Task 23,
    # где FSM ещё не требовался.
    await handler(callback, callback_data, session, actor, svc, state)


@router.callback_query(ConfirmCb.filter())
async def handle_confirm(callback: CallbackQuery, callback_data: ConfirmCb, session):
    """Critical security fix (Task 23 review): раньше здесь проверялось только
    `actor is None` — любой зарегистрированный АКТИВНЫЙ пользователь (даже с
    нулём прав в системе, например LOGISTIC) мог подтвердить кнопкой ЧУЖУЮ
    привилегированную операцию, потому что `_pending_confirms` не был привязан
    ни к праву, ни к создателю. Теперь токен несёт `required_permission`, и
    подтверждающий обязан иметь его — независимо от того, кто инициировал
    операцию (см. `AdminService.confirm_token`)."""
    actor = await UserService(session).get_actor(callback.from_user.id)
    if actor is None:
        await callback.answer("Недостаточно прав", show_alert=True)
        return
    svc = AdminService(session)
    if not callback_data.ok:
        svc.discard(callback_data.t)   # отменённый токен нельзя подтвердить задним числом
        await callback.message.edit_text("Отменено")
        await callback.answer()
        return
    entry = svc.get_pending(callback_data.t)
    if entry is None:
        await callback.message.edit_text("Операция уже выполнена")
        await callback.answer()
        return
    if svc.is_expired(entry):
        svc.discard(callback_data.t)
        await callback.message.edit_text("Время подтверждения истекло, повторите действие")
        await callback.answer()
        return
    if not await svc.permissions.has_permission(actor, entry.required_permission):
        await callback.answer("Недостаточно прав", show_alert=True)
        return
    done = await svc.execute_confirmed(callback_data.t)
    await session.commit()
    await callback.message.edit_text("Выполнено ✅" if done else "Операция уже выполнена")
    await callback.answer()
