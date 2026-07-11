from aiogram import Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from bot.keyboards.admin.confirm import ConfirmCb
from bot.keyboards.admin.main import SECTION_ALIASES, AdminCb, admin_menu_keyboard
from bot.services.admin_service import SECTION_PERMISSIONS, AdminService
from bot.services.user_service import UserService

router = Router(name=__name__)


@router.message(Command("admin"))
async def cmd_admin(message: Message, session):
    actor = await UserService(session).get_actor(message.from_user.id)
    svc = AdminService(session)
    allowed = await svc.visible_sections(actor) if actor else set()
    if not allowed:
        await message.answer("Доступ запрещен")
        return
    await message.answer("🛠 Админ-панель", reply_markup=admin_menu_keyboard(allowed))


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
async def handle_section(callback: CallbackQuery, callback_data: AdminCb, session):
    actor, svc = await resolve_admin(callback, session, callback_data.s)
    if actor is None:
        return
    # диспетчеризация по разделам — подключается в Tasks 24-27
    from bot.handlers.admin import SECTION_HANDLERS
    handler = SECTION_HANDLERS.get(callback_data.s)
    if handler is None:
        await callback.answer("Раздел в разработке")
        return
    await handler(callback, callback_data, session, actor, svc)


@router.callback_query(ConfirmCb.filter())
async def handle_confirm(callback: CallbackQuery, callback_data: ConfirmCb, session):
    actor = await UserService(session).get_actor(callback.from_user.id)
    if actor is None:
        await callback.answer("Недостаточно прав", show_alert=True)
        return
    if not callback_data.ok:
        await callback.message.edit_text("Отменено")
        await callback.answer()
        return
    done = await AdminService(session).execute_confirmed(callback_data.t)
    await session.commit()
    await callback.message.edit_text("Выполнено ✅" if done else "Операция уже выполнена")
    await callback.answer()
