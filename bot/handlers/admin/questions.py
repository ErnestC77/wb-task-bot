"""Раздел админ-панели «❓ Маршрутизация вопросов» (Task 31).

Показывает все 8 настроек категории `questions` (реестр `SETTINGS_REGISTRY`,
Task 7), переиспользуя `render_setting_card`/`category_keys`/`key_by_index`/
`apply_setting_input`/`parse_raw` из `bot/handlers/admin/settings.py` (Task 24)
— это ЧИСТЫЕ функции без встроенной проверки прав, поэтому их можно
переиспользовать под другим правом (`questions.manage`, не `settings.manage`).

Три "получатель"-ключа (`default_receiver_user_id`/`fallback_receiver_user_id`/
`escalation_receiver_user_id`) и два "маршрут"-ключа (`route_by_topic`/
`route_by_category`) редактируются НЕ обычным текстовым вводом, а пикерами:
- получатель — список активных пользователей с `private_chat_available=True`
  (кнопка -> `set_receiver_setting`);
- маршрут — двухшаговый пикер «тема/категория товара -> получатель» (кнопка ->
  `update_route_map`, мержит одну пару в существующий JSON, не перезаписывает
  целиком).
Остальные 3 ключа (`escalation_hours`/`notify_asker_on_answer`/
`allow_complete_with_open_questions`) — обычный текстовый FSM-ввод через
переиспользованные `parse_raw`/`apply_setting_input`.

Как и в разделах Tasks 25-30, навигация использует СОБСТВЕННЫЙ `QstCb`
(prefix="q"), НЕ `AdminCb` — каждый callback этого модуля и каждое
FSM-продолжение сообщением заново проверяет actor + право `questions.manage`.

Отклонение от буквального кода брифа: `update_route_map` вызывал
`AuditService.log(..., new_value_json=str(current))` — у `AuditService.log`
НЕТ параметра `new_value_json` (сигнатура: `new_value=None`, сама сериализует
через `json.dumps` внутри, см. `bot/services/audit_service.py:12-21`).
Буквальный код брифа упал бы `TypeError` при первом же вызове. Исправлено на
`new_value=current`.
"""
from bot.database.models import ArticleCategory, Topic, User
from bot.database.repositories.topic_repository import TopicRepository
from bot.database.repositories.user_repository import UserRepository
from bot.handlers.admin.settings import (
    apply_setting_input, category_keys, key_by_index, parse_raw, render_setting_card,
)
from bot.keyboards.admin.main import AdminCb, admin_menu_keyboard
from bot.keyboards.admin.questions import (
    QstCb, cancel_keyboard, question_setting_card_keyboard, questions_list_keyboard,
    receiver_picker_keyboard, route_key_picker_keyboard, route_user_picker_keyboard,
)
from aiogram import Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select

from bot.services.admin_service import AdminService
from bot.services.audit_service import AuditService
from bot.services.setting_service import SettingService
from bot.services.user_service import UserService
from bot.states.admin_states import AdminStates

router = Router(name=__name__)

# --------------------------------------------------------------------------
# Доменные функции (тестируются напрямую, без callback-обвязки) — сигнатуры и
# основные тела set_receiver_setting/update_route_map заданы брифом Task 31
# (кроме исправленного вызова AuditService.log, см. docstring выше).
# --------------------------------------------------------------------------

RECEIVER_SETTING_KEYS = frozenset({
    "questions.default_receiver_user_id",
    "questions.fallback_receiver_user_id",
    "questions.escalation_receiver_user_id",
})

ROUTE_SETTING_KEYS = frozenset({"questions.route_by_topic", "questions.route_by_category"})


async def set_receiver_setting(session, actor: User, key: str, user_id: int) -> None:
    if key not in RECEIVER_SETTING_KEYS:
        raise KeyError("Недопустимый ключ получателя")
    user = await UserRepository(session).get_by_id(user_id)
    if user is None or not user.is_active:
        raise ValueError("Получатель должен быть активным пользователем")
    if not user.private_chat_available:
        raise ValueError("Пользователь еще не открыл личные сообщения с ботом (/start)")
    await SettingService(session).set(key, user_id, actor.id)
    await AuditService(session).log(actor.id, "questions.set_receiver_setting",
                                    setting_key=key, new_value=user_id)


async def update_route_map(session, actor: User, key: str, map_key: str, user_id: int) -> dict:
    settings = SettingService(session)
    current = dict(await settings.get(key))
    current[map_key] = user_id
    await settings.set(key, current, actor.id)
    await AuditService(session).log(actor.id, "questions.update_route",
                                    setting_key=key, new_value=current)
    return current


# --------------------------------------------------------------------------
# actor-check (каждый QstCb-callback и каждое FSM-сообщение проверяют заново —
# resolve_admin из main.py срабатывает только для AdminCb, не для QstCb)
# --------------------------------------------------------------------------

async def _resolve_actor(callback: CallbackQuery, session) -> tuple[User | None, AdminService | None]:
    actor = await UserService(session).get_actor(callback.from_user.id)
    svc = AdminService(session)
    if actor is None or not await svc.permissions.has_permission(actor, "questions.manage"):
        await callback.answer("Недостаточно прав", show_alert=True)
        return None, None
    return actor, svc


async def _resolve_actor_message(message: Message, session) -> User | None:
    actor = await UserService(session).get_actor(message.from_user.id)
    svc = AdminService(session)
    if actor is None or not await svc.permissions.has_permission(actor, "questions.manage"):
        await message.answer("Недостаточно прав")
        return None
    return actor


async def _active_private_users(session) -> list[tuple[int, str]]:
    users = await UserRepository(session).get_all(include_inactive=False)
    return [(u.id, u.name) for u in users if u.private_chat_available]


# --------------------------------------------------------------------------
# Список / карточка
# --------------------------------------------------------------------------

async def _show_menu(callback: CallbackQuery, actor: User, svc: AdminService) -> None:
    allowed = await svc.visible_sections(actor)
    await callback.message.edit_text("🛠 Админ-панель", reply_markup=admin_menu_keyboard(allowed))
    await callback.answer()


async def _show_list(callback: CallbackQuery, session, page: int) -> None:
    keys = category_keys("questions")
    settings_svc = SettingService(session)
    page_size = max(1, int(await settings_svc.get("general.page_size")))
    total_pages = max(1, -(-len(keys) // page_size))
    page = min(max(1, page), total_pages)
    start = (page - 1) * page_size
    entries: list[tuple[int, str]] = []
    for offset, key in enumerate(keys[start:start + page_size]):
        idx = start + offset
        value = await settings_svc.get(key)
        entries.append((idx, f"{key} = {value}"[:60]))
    await callback.message.edit_text(
        "❓ Маршрутизация вопросов", reply_markup=questions_list_keyboard(entries, page, total_pages))
    await callback.answer()


async def _show_card(callback: CallbackQuery, session, idx: int) -> None:
    try:
        key = key_by_index("questions", idx)
    except KeyError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    text = await render_setting_card(session, key)
    await callback.message.edit_text(text, reply_markup=question_setting_card_keyboard(idx, True))
    await callback.answer()


# --------------------------------------------------------------------------
# Редактирование: пикер получателя / пикер маршрута / обычный текстовый ввод
# --------------------------------------------------------------------------

async def _start_edit(callback: CallbackQuery, session, state: FSMContext | None, idx: int) -> None:
    try:
        key = key_by_index("questions", idx)
    except KeyError as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    if key in RECEIVER_SETTING_KEYS:
        users = await _active_private_users(session)
        if not users:
            await callback.answer(
                "Нет активных пользователей с открытой личкой (/start)", show_alert=True)
            return
        await callback.message.edit_text(
            "Выберите получателя:", reply_markup=receiver_picker_keyboard(idx, users))
        await callback.answer()
        return

    if key in ROUTE_SETTING_KEYS:
        if key == "questions.route_by_topic":
            options = [t.topic_key for t in await TopicRepository(session).get_all(include_inactive=False)]
        else:
            cats = list(await session.scalars(
                select(ArticleCategory).where(ArticleCategory.is_active.is_(True))))
            options = [c.name for c in cats]
        if not options:
            await callback.answer("Нет доступных вариантов для маршрута", show_alert=True)
            return
        await callback.message.edit_text(
            "Выберите тему/категорию:", reply_markup=route_key_picker_keyboard(idx, options))
        await callback.answer()
        return

    # "Простая" настройка (escalation_hours/notify_asker_on_answer/
    # allow_complete_with_open_questions) — обычный текстовый FSM-ввод,
    # переиспользующий apply_setting_input/parse_raw из settings.py (Task 24).
    if state is None:
        await callback.answer("Недоступно", show_alert=True)
        return
    current = await SettingService(session).get(key)
    await state.set_state(AdminStates.waiting_qst_value)
    await state.update_data(setting_key=key, idx=idx)
    await callback.message.edit_text(
        f"Текущее значение {key}: {current}\nВведите новое значение сообщением:",
        reply_markup=cancel_keyboard(idx))
    await callback.answer()


@router.message(AdminStates.waiting_qst_value)
async def handle_qst_value_message(message: Message, session, state: FSMContext) -> None:
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
    ok, msg = await apply_setting_input(session, actor, key, message.text or "", None)
    await session.commit()
    await message.answer(msg)
    if ok:
        await state.clear()


async def _pick_receiver(callback: CallbackQuery, session, actor: User, idx: int, user_id: int) -> None:
    try:
        key = key_by_index("questions", idx)
    except KeyError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    try:
        await set_receiver_setting(session, actor, key, user_id)
    except (KeyError, ValueError) as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await session.commit()
    await _show_card(callback, session, idx)


async def _pick_route_key(callback: CallbackQuery, session, idx: int, map_key: str) -> None:
    users = await _active_private_users(session)
    if not users:
        await callback.answer("Нет активных пользователей с открытой личкой (/start)", show_alert=True)
        return
    await callback.message.edit_text(
        f"Получатель для «{map_key}»:", reply_markup=route_user_picker_keyboard(idx, map_key, users))
    await callback.answer()


async def _pick_route_user(callback: CallbackQuery, session, actor: User, idx: int,
                           map_key: str, user_id: int) -> None:
    try:
        key = key_by_index("questions", idx)
    except KeyError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    if key not in ROUTE_SETTING_KEYS:
        await callback.answer("Недопустимый ключ маршрута", show_alert=True)
        return
    user = await UserRepository(session).get_by_id(user_id)
    if user is None or not user.is_active or not user.private_chat_available:
        await callback.answer("Получатель должен быть активным пользователем с открытой личкой",
                              show_alert=True)
        return
    await update_route_map(session, actor, key, map_key, user_id)
    await session.commit()
    await _show_card(callback, session, idx)


# --------------------------------------------------------------------------
# Точки входа
# --------------------------------------------------------------------------

async def handle_questions_section(callback: CallbackQuery, callback_data: AdminCb, session,
                                   actor: User, svc: AdminService,
                                   state: FSMContext | None = None) -> None:
    """Точка входа для AdminCb.s == "qst" (пункт меню «❓ Маршрутизация
    вопросов»). Право questions.manage уже проверено resolve_admin/
    handle_section до вызова. Вся дальнейшая навигация уходит на QstCb (см.
    handle_qst_callback ниже), который проверяет право заново."""
    if callback_data.a == "menu":
        await _show_menu(callback, actor, svc)
    else:
        await _show_list(callback, session, 1)


@router.callback_query(QstCb.filter())
async def handle_qst_callback(callback: CallbackQuery, callback_data: QstCb, session,
                              state: FSMContext | None = None) -> None:
    actor, svc = await _resolve_actor(callback, session)
    if actor is None:
        return
    action = callback_data.a
    # Навигационные действия покидают контекст редактирования — обязаны
    # сбросить FSM (урок Task 28/30: без сброса следующий текст молча
    # применился бы как значение).
    if action in ("list", "card") and state is not None:
        await state.clear()
    if action == "list":
        await _show_list(callback, session, callback_data.p)
    elif action == "card":
        await _show_card(callback, session, callback_data.id)
    elif action == "edit":
        await _start_edit(callback, session, state, callback_data.id)
    elif action == "setrecv":
        await _pick_receiver(callback, session, actor, callback_data.id, callback_data.id2)
    elif action == "routekey":
        await _pick_route_key(callback, session, callback_data.id, callback_data.k)
    elif action == "routeset":
        await _pick_route_user(callback, session, actor, callback_data.id,
                               callback_data.k, callback_data.id2)
    elif action == "noop":
        await callback.answer()
    else:
        await callback.answer("Неизвестное действие", show_alert=True)
