"""Раздел админ-панели «👥 Пользователи и роли» (Task 25).

Навигация: список пользователей (пагинация general.page_size, неактивные
помечены «(неактивен)») -> карточка (имя, username, роль, активность,
private_chat_available, число открытых задач) -> операции: добавить по
Telegram ID (FSM add_id -> add_name -> add_role), изменить имя/username/роль
(FSM для имени/username, кнопки для роли), активировать/деактивировать
(деактивация — ТОЛЬКО через AdminService.confirm_token, физического удаления
нет — Global Constraint плана), granular permissions для partner/owner
(чек-лист всех 12 PERMISSION_KEYS, toggle через PermissionService.grant/
revoke; owner, снимающий право САМ С СОБОЙ, — через отдельный confirm_token,
т.к. PermissionService.revoke бросает LastOwnerPermissionError без
force=True), просмотр открытых задач пользователя и их переназначение,
«✉ Проверить личку» (пробный send_private, обновляет private_chat_available).

Навигация внутри раздела использует СОБСТВЕННЫЙ `UsrCb` (prefix="u"), а НЕ
`AdminCb` — см. docstring в bot/keyboards/admin/users.py. Поэтому, в отличие
от раздела "Настройки" (который целиком проходит через `resolve_admin` в
main.py), КАЖДЫЙ callback этого раздела (`handle_usr_callback`, единая точка
входа для всех UsrCb) и КАЖДОЕ FSM-продолжение сообщением заново проверяет
actor и право users.manage — resolve_admin для UsrCb.filter() не
срабатывает."""
from aiogram import Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.database.models import Role, TaskInstance, User
from bot.database.repositories.task_repository import OPEN_STATUSES, TaskRepository
from bot.database.repositories.user_repository import UserRepository
from bot.keyboards.admin.confirm import confirm_keyboard
from bot.keyboards.admin.main import AdminCb, admin_menu_keyboard
from bot.keyboards.admin.users import (
    ROLE_TITLES, UsrCb, permission_checklist_keyboard, reassign_target_keyboard,
    reassign_task_list_keyboard, role_picker_keyboard, user_card_keyboard, users_list_keyboard,
)
from bot.services.admin_service import AdminService
from bot.services.audit_service import AuditService
from bot.services.delivery_service import DeliveryService
from bot.services.permission_service import LastOwnerPermissionError, PermissionService
from bot.services.setting_service import SettingService
from bot.services.user_service import UserService
from bot.states.admin_states import AdminStates
from bot.utils.html_utils import code, html_escape
from bot.utils.permissions import PERMISSION_KEYS, PERMISSION_TITLES
from bot.utils.validation import validate_int

router = Router(name=__name__)

PERMISSION_KEYS_SORTED: list[str] = sorted(PERMISSION_KEYS)
ROLE_VALUES: frozenset[str] = frozenset(r.value for r in Role)
PERMISSION_ROLES: tuple[str, ...] = (Role.OWNER, Role.PARTNER)  # только для них есть смысл


# --------------------------------------------------------------------------
# Доменные функции (тестируются напрямую, без callback-обвязки) — сигнатуры и
# тела заданы брифом Task 25 дословно.
# --------------------------------------------------------------------------

async def set_question_receiver(session, actor: User, user_id: int) -> None:
    user = await UserRepository(session).get_by_id(user_id)
    if user is None or not user.is_active:
        raise ValueError("Получатель должен быть активным пользователем")
    if not user.private_chat_available:
        raise ValueError("Пользователь еще не открыл личные сообщения с ботом (/start)")
    await SettingService(session).set("questions.default_receiver_user_id",
                                      user_id, actor.id)
    await AuditService(session).log(actor.id, "questions.set_receiver",
                                    entity_type="user", entity_id=str(user_id))


async def reassign_task(session, actor: User, instance_id: int,
                        new_user_id: int) -> TaskInstance:
    repo = TaskRepository(session)
    inst = await repo.get_instance(instance_id)
    if inst is None or inst.status not in OPEN_STATUSES:
        raise ValueError("Переназначить можно только открытую задачу")
    new_user = await UserRepository(session).get_by_id(new_user_id)
    if new_user is None or not new_user.is_active:
        raise ValueError("Новый ответственный должен быть активным пользователем")
    old = inst.responsible_user_id
    inst.responsible_user_id = new_user_id
    inst.responsible_name_snapshot = new_user.name
    await session.flush()
    await AuditService(session).log(actor.id, "task.reassign",
                                    entity_type="task_instance", entity_id=str(instance_id),
                                    old_value=old, new_value=new_user_id)
    return inst


async def render_permission_checklist(session, user_id: int) -> str:
    perms = await PermissionService(session).list_permissions(user_id)
    lines = ["Права:"]
    for key in sorted(PERMISSION_KEYS):
        mark = "✅" if perms[key] else "▫"
        lines.append(f"{mark} {key} — {PERMISSION_TITLES[key]}")
    return "\n".join(lines)


async def render_user_card(session, user: User) -> str:
    open_count = len(await TaskRepository(session).get_open_instances_for_user(user.id))
    lines = [
        f"👤 {html_escape(user.name)}",
        f"Username: {html_escape('@' + user.username) if user.username else '—'}",
        f"Роль: {ROLE_TITLES.get(user.role, user.role)}",
        f"Telegram ID: {code(user.telegram_id)}",
        f"Статус: {'активен' if user.is_active else 'неактивен'}",
        f"Личные сообщения доступны: {'да' if user.private_chat_available else 'нет'}",
        f"Открытых задач: {open_count}",
    ]
    return "\n".join(lines)


def _show_permissions_button(user: User) -> bool:
    return user.role in PERMISSION_ROLES


# --------------------------------------------------------------------------
# actor-check (каждый UsrCb-callback и каждое FSM-сообщение проверяют заново —
# resolve_admin из main.py срабатывает только для AdminCb, не для UsrCb)
# --------------------------------------------------------------------------

async def _resolve_actor(callback: CallbackQuery, session) -> tuple[User | None, AdminService | None]:
    actor = await UserService(session).get_actor(callback.from_user.id)
    svc = AdminService(session)
    if actor is None or not await svc.permissions.has_permission(actor, "users.manage"):
        await callback.answer("Недостаточно прав", show_alert=True)
        return None, None
    return actor, svc


async def _resolve_actor_message(message: Message, session) -> User | None:
    actor = await UserService(session).get_actor(message.from_user.id)
    svc = AdminService(session)
    if actor is None or not await svc.permissions.has_permission(actor, "users.manage"):
        await message.answer("Недостаточно прав")
        return None
    return actor


# --------------------------------------------------------------------------
# Список / карточка
# --------------------------------------------------------------------------

async def _show_menu(callback: CallbackQuery, actor: User, svc: AdminService) -> None:
    allowed = await svc.visible_sections(actor)
    await callback.message.edit_text("🛠 Админ-панель", reply_markup=admin_menu_keyboard(allowed))
    await callback.answer()


async def _show_users_list(callback: CallbackQuery, session, page: int) -> None:
    users = sorted(await UserRepository(session).get_all(include_inactive=True),
                   key=lambda u: u.name)
    settings_svc = SettingService(session)
    page_size = max(1, int(await settings_svc.get("general.page_size")))
    total_pages = max(1, -(-len(users) // page_size))
    page = min(max(1, page), total_pages)
    start = (page - 1) * page_size
    entries = [(u.id, u.name if u.is_active else f"{u.name} (неактивен)")
               for u in users[start:start + page_size]]
    await callback.message.edit_text(
        "👥 Пользователи", reply_markup=users_list_keyboard(entries, page, total_pages))
    await callback.answer()


async def _show_card(callback: CallbackQuery, session, user_id: int) -> None:
    user = await UserRepository(session).get_by_id(user_id)
    if user is None:
        await callback.answer("Пользователь не найден", show_alert=True)
        return
    text = await render_user_card(session, user)
    await callback.message.edit_text(
        text, reply_markup=user_card_keyboard(user.id, user.is_active, _show_permissions_button(user)))
    await callback.answer()


# --------------------------------------------------------------------------
# Добавление пользователя (FSM add_id -> add_name -> add_role)
# --------------------------------------------------------------------------

async def _start_add(callback: CallbackQuery, state: FSMContext | None) -> None:
    if state is None:
        await callback.answer("Недоступно", show_alert=True)
        return
    await state.set_state(AdminStates.waiting_add_id)
    await callback.message.edit_text("Введите Telegram ID нового пользователя:")
    await callback.answer()


@router.message(AdminStates.waiting_add_id)
async def handle_add_id_message(message: Message, session, state: FSMContext) -> None:
    actor = await _resolve_actor_message(message, session)
    if actor is None:
        await state.clear()
        return
    try:
        telegram_id = validate_int(message.text or "")
    except ValueError as exc:
        await message.answer(str(exc))
        return
    await state.update_data(new_telegram_id=telegram_id)
    await state.set_state(AdminStates.waiting_add_name)
    await message.answer("Введите имя пользователя:")


@router.message(AdminStates.waiting_add_name)
async def handle_add_name_message(message: Message, session, state: FSMContext) -> None:
    actor = await _resolve_actor_message(message, session)
    if actor is None:
        await state.clear()
        return
    name = (message.text or "").strip()
    if not name:
        await message.answer("Имя не может быть пустым, введите ещё раз:")
        return
    await state.update_data(new_name=name)
    await state.set_state(AdminStates.waiting_add_role)
    await message.answer("Выберите роль:", reply_markup=role_picker_keyboard(0))


# --------------------------------------------------------------------------
# Роль (общая точка для завершения добавления И для смены роли существующего
# пользователя — различаются по target_id: 0 == часть add-flow, т.к. id
# пользователей в БД автоинкремент с 1 и никогда не равен 0)
# --------------------------------------------------------------------------

async def _show_role_picker(callback: CallbackQuery, session, user_id: int) -> None:
    user = await UserRepository(session).get_by_id(user_id)
    if user is None:
        await callback.answer("Пользователь не найден", show_alert=True)
        return
    await callback.message.edit_text("Выберите новую роль:", reply_markup=role_picker_keyboard(user_id))
    await callback.answer()


async def _apply_role(callback: CallbackQuery, session, state: FSMContext | None,
                      actor: User, target_id: int, role: str) -> None:
    if role not in ROLE_VALUES:
        await callback.answer("Неизвестная роль", show_alert=True)
        return
    if target_id == 0:
        await _finish_add(callback, session, state, actor, role)
        return
    user = await UserRepository(session).get_by_id(target_id)
    if user is None:
        await callback.answer("Пользователь не найден", show_alert=True)
        return
    old_role = user.role
    user.role = role
    await session.flush()
    await AuditService(session).log(actor.id, "user.role_change", entity_type="user",
                                    entity_id=str(user.id), old_value=old_role, new_value=role)
    await session.commit()
    text = await render_user_card(session, user)
    await callback.message.edit_text(
        text, reply_markup=user_card_keyboard(user.id, user.is_active, _show_permissions_button(user)))
    await callback.answer("Роль обновлена ✅")


async def _finish_add(callback: CallbackQuery, session, state: FSMContext | None,
                      actor: User, role: str) -> None:
    if state is None:
        await callback.answer("Недоступно", show_alert=True)
        return
    data = await state.get_data()
    telegram_id = data.get("new_telegram_id")
    name = data.get("new_name")
    if telegram_id is None or name is None:
        await callback.answer("Сессия добавления утеряна, начните заново", show_alert=True)
        await state.clear()
        return
    user = await UserRepository(session).upsert(telegram_id=telegram_id, name=name, role=role)
    await AuditService(session).log(actor.id, "user.add", entity_type="user",
                                    entity_id=str(user.id), new_value=role)
    await session.commit()
    await state.clear()
    text = await render_user_card(session, user)
    await callback.message.edit_text(
        text, reply_markup=user_card_keyboard(user.id, user.is_active, _show_permissions_button(user)))
    await callback.answer("Пользователь добавлен ✅")


# --------------------------------------------------------------------------
# Изменение имени / username (FSM)
# --------------------------------------------------------------------------

async def _start_edit_name(callback: CallbackQuery, session, state: FSMContext | None,
                           user_id: int) -> None:
    if state is None:
        await callback.answer("Недоступно", show_alert=True)
        return
    user = await UserRepository(session).get_by_id(user_id)
    if user is None:
        await callback.answer("Пользователь не найден", show_alert=True)
        return
    await state.set_state(AdminStates.waiting_edit_name)
    await state.update_data(edit_user_id=user_id)
    await callback.message.edit_text(
        f"Текущее имя: {html_escape(user.name)}\nВведите новое имя:")
    await callback.answer()


async def _start_edit_username(callback: CallbackQuery, session, state: FSMContext | None,
                               user_id: int) -> None:
    if state is None:
        await callback.answer("Недоступно", show_alert=True)
        return
    user = await UserRepository(session).get_by_id(user_id)
    if user is None:
        await callback.answer("Пользователь не найден", show_alert=True)
        return
    await state.set_state(AdminStates.waiting_edit_username)
    await state.update_data(edit_user_id=user_id)
    current = f"@{user.username}" if user.username else "—"
    await callback.message.edit_text(
        f"Текущий username: {html_escape(current)}\nВведите новый username (без @):")
    await callback.answer()


@router.message(AdminStates.waiting_edit_name)
async def handle_edit_name_message(message: Message, session, state: FSMContext) -> None:
    actor = await _resolve_actor_message(message, session)
    if actor is None:
        await state.clear()
        return
    data = await state.get_data()
    user_id = data.get("edit_user_id")
    name = (message.text or "").strip()
    if not name:
        await message.answer("Имя не может быть пустым, введите ещё раз:")
        return
    user = await UserRepository(session).get_by_id(user_id) if user_id is not None else None
    if user is None:
        await message.answer("Пользователь не найден")
        await state.clear()
        return
    old_name = user.name
    user.name = name
    await session.flush()
    await AuditService(session).log(actor.id, "user.name_change", entity_type="user",
                                    entity_id=str(user.id), old_value=old_name, new_value=name)
    await session.commit()
    await state.clear()
    await message.answer("Имя обновлено ✅")


@router.message(AdminStates.waiting_edit_username)
async def handle_edit_username_message(message: Message, session, state: FSMContext) -> None:
    actor = await _resolve_actor_message(message, session)
    if actor is None:
        await state.clear()
        return
    data = await state.get_data()
    user_id = data.get("edit_user_id")
    username = (message.text or "").strip().lstrip("@") or None
    user = await UserRepository(session).get_by_id(user_id) if user_id is not None else None
    if user is None:
        await message.answer("Пользователь не найден")
        await state.clear()
        return
    old_username = user.username
    user.username = username
    await session.flush()
    await AuditService(session).log(actor.id, "user.username_change", entity_type="user",
                                    entity_id=str(user.id), old_value=old_username, new_value=username)
    await session.commit()
    await state.clear()
    await message.answer("Username обновлён ✅")


# --------------------------------------------------------------------------
# Активация / деактивация (деактивация — ОПАСНАЯ операция, только через
# confirm_token; Global Constraint: физического удаления нет и не будет)
# --------------------------------------------------------------------------

async def _toggle_active(callback: CallbackQuery, session, actor: User,
                         svc: AdminService, user_id: int) -> None:
    user = await UserRepository(session).get_by_id(user_id)
    if user is None:
        await callback.answer("Пользователь не найден", show_alert=True)
        return
    if not user.is_active:
        user.is_active = True
        await session.flush()
        await AuditService(session).log(actor.id, "user.activate", entity_type="user",
                                        entity_id=str(user.id))
        await session.commit()
        text = await render_user_card(session, user)
        await callback.message.edit_text(
            text, reply_markup=user_card_keyboard(user.id, True, _show_permissions_button(user)))
        await callback.answer("Активирован ✅")
        return

    async def op() -> None:
        await UserRepository(session).deactivate(user_id)
        await AuditService(session).log(actor.id, "user.deactivate", entity_type="user",
                                        entity_id=str(user_id))

    token = svc.confirm_token(f"user.deactivate.{user_id}", op,
                              required_permission="users.manage", creator_actor_id=actor.id)
    await callback.message.edit_text(
        f"Деактивировать пользователя {html_escape(user.name)}? "
        "Физическое удаление недоступно, доступ можно будет вернуть позже.",
        reply_markup=confirm_keyboard(token))
    await callback.answer()


# --------------------------------------------------------------------------
# Granular permissions (только owner/partner) — чек-лист всех 12
# PERMISSION_KEYS, индекс права уходит в UsrCb.k строкой (k=str(idx)),
# user_id остаётся отдельным полем UsrCb.id (см. docstring keyboards/users.py)
# --------------------------------------------------------------------------

async def _show_permissions(callback: CallbackQuery, session, user_id: int) -> None:
    user = await UserRepository(session).get_by_id(user_id)
    if user is None:
        await callback.answer("Пользователь не найден", show_alert=True)
        return
    perms = await PermissionService(session).list_permissions(user_id)
    text = await render_permission_checklist(session, user_id)
    await callback.message.edit_text(
        text, reply_markup=permission_checklist_keyboard(user_id, PERMISSION_KEYS_SORTED, perms))
    await callback.answer()


async def _toggle_permission(callback: CallbackQuery, session, actor: User,
                             svc: AdminService, user_id: int, idx_str: str) -> None:
    try:
        idx = int(idx_str)
    except ValueError:
        await callback.answer("Некорректный индекс права", show_alert=True)
        return
    if not 0 <= idx < len(PERMISSION_KEYS_SORTED):
        await callback.answer("Неизвестное право", show_alert=True)
        return
    key = PERMISSION_KEYS_SORTED[idx]
    target = await UserRepository(session).get_by_id(user_id)
    if target is None:
        await callback.answer("Пользователь не найден", show_alert=True)
        return
    perm_svc = PermissionService(session)
    # ВАЖНО: решение grant/revoke принимается по ЭФФЕКТИВНОМУ праву
    # (has_permission), а не по сырым строкам PermissionService.list_permissions
    # (та возвращает False для owner без явного override — owner имеет право
    # неявно, «true, пока явно не отозвано»). Если бы toggle опирался на
    # list_permissions, owner никогда не смог бы попасть в ветку revoke() для
    # неявных прав — всегда шёл бы grant(), а LastOwnerPermissionError (и,
    # соответственно, обязательный confirm_token для self-revoke) никогда бы
    # не сработал. render_permission_checklist ниже намеренно оставлен как в
    # брифе (сырые метки ✅/▫ по list_permissions) — это только отображение.
    currently_allowed = await perm_svc.has_permission(target, key)
    if currently_allowed:
        try:
            await perm_svc.revoke(actor, user_id, key)
        except LastOwnerPermissionError:
            # Owner снимает право с самого себя — отдельный confirm_token,
            # отличный от обычного мгновенного toggle (требование брифа).
            async def op() -> None:
                await PermissionService(session).revoke(actor, user_id, key, force=True)

            token = svc.confirm_token(
                f"perm.selfrevoke.{user_id}.{key}", op,
                required_permission="users.manage", creator_actor_id=actor.id)
            await callback.message.edit_text(
                f"Снять с себя право «{PERMISSION_TITLES.get(key, key)}»? "
                "Это последнее подтверждённое право владельца, снятое таким образом.",
                reply_markup=confirm_keyboard(token))
            await callback.answer()
            return
    else:
        await perm_svc.grant(actor, user_id, key)
    await session.commit()
    await _show_permissions(callback, session, user_id)


async def _toggle(callback: CallbackQuery, session, actor: User, svc: AdminService,
                  user_id: int, k: str | None) -> None:
    # k=None (default — см. UsrCb.k) означает «переключить активность»;
    # k=str(idx) (всегда непустая строка "0".."11") — переключить конкретное
    # право по индексу в PERMISSION_KEYS_SORTED.
    if not k:
        await _toggle_active(callback, session, actor, svc, user_id)
    else:
        await _toggle_permission(callback, session, actor, svc, user_id, k)


# --------------------------------------------------------------------------
# Открытые задачи пользователя и переназначение
# --------------------------------------------------------------------------

async def _show_reassign_list(callback: CallbackQuery, session, user_id: int, page: int) -> None:
    user = await UserRepository(session).get_by_id(user_id)
    if user is None:
        await callback.answer("Пользователь не найден", show_alert=True)
        return
    instances = await TaskRepository(session).get_open_instances_for_user(user_id)
    settings_svc = SettingService(session)
    page_size = max(1, int(await settings_svc.get("general.page_size")))
    total_pages = max(1, -(-len(instances) // page_size)) if instances else 1
    page = min(max(1, page), total_pages)
    start = (page - 1) * page_size
    entries = []
    for inst in instances[start:start + page_size]:
        due = inst.due_at.strftime("%d.%m %H:%M") if inst.due_at else "—"
        entries.append((inst.id, f"{inst.title_snapshot[:40]} (до {due})"))
    title = "📋 Открытые задачи" if entries else "📋 Открытых задач нет"
    await callback.message.edit_text(
        title, reply_markup=reassign_task_list_keyboard(user_id, entries, page, total_pages))
    await callback.answer()


async def _reassign_pick(callback: CallbackQuery, session, instance_id: int,
                         origin_user_id_str: str) -> None:
    try:
        origin_user_id = int(origin_user_id_str)
    except ValueError:
        await callback.answer("Некорректные данные", show_alert=True)
        return
    inst = await TaskRepository(session).get_instance(instance_id)
    if inst is None or inst.status not in OPEN_STATUSES:
        await callback.answer("Задача уже закрыта", show_alert=True)
        return
    candidates = await UserRepository(session).get_all(include_inactive=False)
    entries = [(u.id, u.name) for u in candidates if u.id != inst.responsible_user_id]
    if not entries:
        await callback.answer("Нет доступных пользователей для переназначения", show_alert=True)
        return
    await callback.message.edit_text(
        f"Переназначить «{html_escape(inst.title_snapshot)}» — выберите нового ответственного:",
        reply_markup=reassign_target_keyboard(instance_id, origin_user_id, entries))
    await callback.answer()


async def _reassign_do(callback: CallbackQuery, session, actor: User,
                       instance_id: int, new_user_id_str: str) -> None:
    try:
        new_user_id = int(new_user_id_str)
    except ValueError:
        await callback.answer("Некорректные данные", show_alert=True)
        return
    try:
        inst = await reassign_task(session, actor, instance_id, new_user_id)
    except ValueError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await session.commit()
    await callback.message.edit_text(
        f"Задача переназначена на {html_escape(inst.responsible_name_snapshot)} ✅")
    await callback.answer()


# --------------------------------------------------------------------------
# «✉ Проверить личку»
# --------------------------------------------------------------------------

async def _check_private_message(callback: CallbackQuery, session, actor: User,
                                 user_id: int) -> None:
    user = await UserRepository(session).get_by_id(user_id)
    if user is None:
        await callback.answer("Пользователь не найден", show_alert=True)
        return
    delivery = DeliveryService(session, callback.bot)
    ok, _ = await delivery.send_private(
        user.telegram_id, "✅ Проверка личных сообщений от админ-панели WB Task Bot.")
    user.private_chat_available = ok
    await session.flush()
    await AuditService(session).log(actor.id, "user.check_private_chat", entity_type="user",
                                    entity_id=str(user_id), new_value=ok)
    await session.commit()
    text = await render_user_card(session, user)
    await callback.message.edit_text(
        text, reply_markup=user_card_keyboard(user.id, user.is_active, _show_permissions_button(user)))
    await callback.answer("Личные сообщения доступны ✅" if ok else "Не удалось отправить ❌")


# --------------------------------------------------------------------------
# Точки входа
# --------------------------------------------------------------------------

async def handle_users_section(callback: CallbackQuery, callback_data: AdminCb, session,
                               actor: User, svc: AdminService,
                               state: FSMContext | None = None) -> None:
    """Точка входа для AdminCb.s == "usr" — вход в раздел из главного меню
    админ-панели и «Назад» с самого верхнего уровня (список пользователей).
    Право users.manage уже проверено resolve_admin/handle_section до вызова.
    Вся дальнейшая навигация уходит на UsrCb (см. handle_usr_callback ниже),
    который проверяет право заново."""
    if callback_data.a == "menu":
        await _show_menu(callback, actor, svc)
    else:
        await _show_users_list(callback, session, 1)


@router.callback_query(UsrCb.filter())
async def handle_usr_callback(callback: CallbackQuery, callback_data: UsrCb, session,
                              state: FSMContext | None = None) -> None:
    actor, svc = await _resolve_actor(callback, session)
    if actor is None:
        return
    action = callback_data.a
    if action == "list":
        await _show_users_list(callback, session, callback_data.p)
    elif action == "card":
        await _show_card(callback, session, callback_data.id)
    elif action == "add":
        await _start_add(callback, state)
    elif action == "editname":
        await _start_edit_name(callback, session, state, callback_data.id)
    elif action == "editun":
        await _start_edit_username(callback, session, state, callback_data.id)
    elif action == "rolepick":
        await _show_role_picker(callback, session, callback_data.id)
    elif action == "role":
        await _apply_role(callback, session, state, actor, callback_data.id, callback_data.k)
    elif action == "toggle":
        await _toggle(callback, session, actor, svc, callback_data.id, callback_data.k)
    elif action == "perm":
        await _show_permissions(callback, session, callback_data.id)
    elif action == "reassign":
        await _show_reassign_list(callback, session, callback_data.id, callback_data.p)
    elif action == "reassign_pick":
        await _reassign_pick(callback, session, callback_data.id, callback_data.k)
    elif action == "reassign_do":
        await _reassign_do(callback, session, actor, callback_data.id, callback_data.k)
    elif action == "checkpm":
        await _check_private_message(callback, session, actor, callback_data.id)
    elif action == "noop":
        await callback.answer()
    else:
        await callback.answer("Неизвестное действие", show_alert=True)
