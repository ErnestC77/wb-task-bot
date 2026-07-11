"""Раздел админ-панели «🗂 Темы Telegram» (Task 26).

Навигация: список тем (6 стартовых из миграции 0002 + добавленные позже,
пагинация general.page_size, неактивные помечены «(неактивна)») -> карточка
(topic_key, название, message_thread_id, тип событий csv, активность,
последний результат тестовой отправки) -> операции: изменить название,
изменить message_thread_id (после сохранения — АВТОМАТИЧЕСКАЯ тестовая
отправка через `TopicService.send_test_message`, результат сразу виден в
карточке), изменить тип событий, активировать/деактивировать (деактивация —
ОПАСНАЯ операция, только через `AdminService.confirm_token`, по аналогии с
деактивацией пользователя в Task 25; активация — мгновенная), «📨 Тестовая
отправка» вручную (тот же `TopicService.send_test_message`, без изменения
message_thread_id).

Навигация внутри раздела использует СОБСТВЕННЫЙ `TopCb` (prefix="tp"), а НЕ
`AdminCb` — см. docstring в bot/keyboards/admin/topics.py. Поэтому, как и в
разделе "Пользователи и роли" (Task 25), КАЖДЫЙ callback этого раздела
(`handle_top_callback`, единая точка входа для всех TopCb) и КАЖДОЕ
FSM-продолжение сообщением заново проверяет actor и право topics.manage —
resolve_admin для TopCb.filter() не срабатывает.

Изменение полей темы — прямое присваивание конкретному, заранее известному
атрибуту (`topic.topic_name = ...`, ровно как `user.name = ...` в
bot/handlers/admin/users.py), НЕ generic `setattr` по строковому ключу из
пользовательского ввода — никакого whitelist-обхода тут нет и быть не может,
т.к. каждый вызов явно называет поле."""
from aiogram import Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.database.models import Topic, User
from bot.database.repositories.topic_repository import TopicRepository
from bot.keyboards.admin.confirm import confirm_keyboard
from bot.keyboards.admin.main import AdminCb, admin_menu_keyboard
from bot.keyboards.admin.topics import TopCb, topic_card_keyboard, topics_list_keyboard
from bot.services.admin_service import AdminService
from bot.services.audit_service import AuditService
from bot.services.setting_service import SettingService
from bot.services.topic_service import TopicService
from bot.services.user_service import UserService
from bot.states.admin_states import AdminStates
from bot.utils.html_utils import code, html_escape
from bot.utils.validation import validate_int

router = Router(name=__name__)

# Ephemeral UI-состояние «последняя тестовая отправка темы» — НЕ персистентное
# (нет отдельной колонки в Topic), сбрасывается при рестарте процесса. Тот же
# паттерн модульного словаря для состояния админ-панели, что и
# `_pending_confirms` в bot/services/admin_service.py.
_LAST_TEST_RESULT: dict[str, bool] = {}


# --------------------------------------------------------------------------
# Доменная функция (тестируется напрямую, без callback-обвязки) — сигнатура и
# тело заданы брифом Task 26 дословно.
# --------------------------------------------------------------------------

async def apply_thread_id_change(session, bot, actor: User, topic_key: str,
                                 thread_id: int) -> bool:
    repo = TopicRepository(session)
    topic = await repo.get_by_key(topic_key)
    old = topic.message_thread_id if topic else None
    await repo.set_thread_id(topic_key, thread_id)
    ok = await TopicService(session, bot).send_test_message(topic_key)
    _LAST_TEST_RESULT[topic_key] = ok
    await AuditService(session).log(actor.id, "topic.set_thread",
                                    entity_type="topic", entity_id=topic_key,
                                    old_value=old, new_value=thread_id,
                                    result="ok" if ok else "error")
    return ok


# --------------------------------------------------------------------------
# Отображение
# --------------------------------------------------------------------------

async def render_topic_card(session, topic: Topic) -> str:
    lines = [
        f"🗂 {html_escape(topic.topic_name)}",
        f"Ключ: {code(topic.topic_key)}",
        f"Message thread ID: {code(topic.message_thread_id) if topic.message_thread_id is not None else '—'}",
        f"Тип событий: {html_escape(topic.event_types) if topic.event_types else '—'}",
        f"Статус: {'активна' if topic.is_active else 'неактивна'}",
    ]
    if topic.topic_key in _LAST_TEST_RESULT:
        ok = _LAST_TEST_RESULT[topic.topic_key]
        lines.append(f"Последняя тестовая отправка: {'✅ успешно' if ok else '❌ ошибка'}")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# actor-check (каждый TopCb-callback и каждое FSM-сообщение проверяют заново —
# resolve_admin из main.py срабатывает только для AdminCb, не для TopCb)
# --------------------------------------------------------------------------

async def _resolve_actor(callback: CallbackQuery, session) -> tuple[User | None, AdminService | None]:
    actor = await UserService(session).get_actor(callback.from_user.id)
    svc = AdminService(session)
    if actor is None or not await svc.permissions.has_permission(actor, "topics.manage"):
        await callback.answer("Недостаточно прав", show_alert=True)
        return None, None
    return actor, svc


async def _resolve_actor_message(message: Message, session) -> User | None:
    actor = await UserService(session).get_actor(message.from_user.id)
    svc = AdminService(session)
    if actor is None or not await svc.permissions.has_permission(actor, "topics.manage"):
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


async def _show_topics_list(callback: CallbackQuery, session, page: int) -> None:
    topics = sorted(await TopicRepository(session).get_all(include_inactive=True),
                    key=lambda t: t.topic_key)
    settings_svc = SettingService(session)
    page_size = max(1, int(await settings_svc.get("general.page_size")))
    total_pages = max(1, -(-len(topics) // page_size))
    page = min(max(1, page), total_pages)
    start = (page - 1) * page_size
    entries = [(t.id, t.topic_name if t.is_active else f"{t.topic_name} (неактивна)")
               for t in topics[start:start + page_size]]
    await callback.message.edit_text(
        "🗂 Темы Telegram", reply_markup=topics_list_keyboard(entries, page, total_pages))
    await callback.answer()


async def _show_card(callback: CallbackQuery, session, topic_id: int) -> None:
    topic = await TopicRepository(session).get_by_id(topic_id)
    if topic is None:
        await callback.answer("Тема не найдена", show_alert=True)
        return
    text = await render_topic_card(session, topic)
    await callback.message.edit_text(
        text, reply_markup=topic_card_keyboard(topic.id, topic.is_active))
    await callback.answer()


# --------------------------------------------------------------------------
# Изменение названия (FSM)
# --------------------------------------------------------------------------

async def _start_edit_name(callback: CallbackQuery, session, state: FSMContext | None,
                           topic_id: int) -> None:
    if state is None:
        await callback.answer("Недоступно", show_alert=True)
        return
    topic = await TopicRepository(session).get_by_id(topic_id)
    if topic is None:
        await callback.answer("Тема не найдена", show_alert=True)
        return
    await state.set_state(AdminStates.waiting_topic_name)
    await state.update_data(topic_id=topic_id)
    await callback.message.edit_text(
        f"Текущее название: {html_escape(topic.topic_name)}\nВведите новое название:")
    await callback.answer()


@router.message(AdminStates.waiting_topic_name)
async def handle_topic_name_message(message: Message, session, state: FSMContext) -> None:
    actor = await _resolve_actor_message(message, session)
    if actor is None:
        await state.clear()
        return
    data = await state.get_data()
    topic_id = data.get("topic_id")
    name = (message.text or "").strip()
    if not name:
        await message.answer("Название не может быть пустым, введите ещё раз:")
        return
    topic = await TopicRepository(session).get_by_id(topic_id) if topic_id is not None else None
    if topic is None:
        await message.answer("Тема не найдена")
        await state.clear()
        return
    old_name = topic.topic_name
    topic.topic_name = name
    await session.flush()
    await AuditService(session).log(actor.id, "topic.name_change", entity_type="topic",
                                    entity_id=topic.topic_key, old_value=old_name, new_value=name)
    await session.commit()
    await state.clear()
    await message.answer("Название обновлено ✅")


# --------------------------------------------------------------------------
# Изменение message_thread_id (FSM) — после сохранения АВТОМАТИЧЕСКАЯ
# тестовая отправка через apply_thread_id_change (см. выше, сигнатура брифа).
# --------------------------------------------------------------------------

async def _start_edit_thread(callback: CallbackQuery, session, state: FSMContext | None,
                             topic_id: int) -> None:
    if state is None:
        await callback.answer("Недоступно", show_alert=True)
        return
    topic = await TopicRepository(session).get_by_id(topic_id)
    if topic is None:
        await callback.answer("Тема не найдена", show_alert=True)
        return
    await state.set_state(AdminStates.waiting_topic_thread)
    await state.update_data(topic_id=topic_id)
    current = topic.message_thread_id if topic.message_thread_id is not None else "—"
    await callback.message.edit_text(
        f"Текущий message_thread_id: {code(current)}\n"
        "Введите новый message_thread_id (целое число):")
    await callback.answer()


@router.message(AdminStates.waiting_topic_thread)
async def handle_topic_thread_message(message: Message, session, state: FSMContext) -> None:
    actor = await _resolve_actor_message(message, session)
    if actor is None:
        await state.clear()
        return
    data = await state.get_data()
    topic_id = data.get("topic_id")
    topic = await TopicRepository(session).get_by_id(topic_id) if topic_id is not None else None
    if topic is None:
        await message.answer("Тема не найдена")
        await state.clear()
        return
    try:
        thread_id = validate_int(message.text or "")
    except ValueError as exc:
        await message.answer(str(exc))
        return
    ok = await apply_thread_id_change(session, message.bot, actor, topic.topic_key, thread_id)
    await session.commit()
    await state.clear()
    result = "✅ тестовое сообщение отправлено и удалено" if ok else "❌ ошибка тестовой отправки"
    await message.answer(f"message_thread_id сохранён. Тестовая отправка: {result}")


# --------------------------------------------------------------------------
# Изменение типа событий (FSM) — свободная csv-строка (tasks,reports,...)
# --------------------------------------------------------------------------

async def _start_edit_events(callback: CallbackQuery, session, state: FSMContext | None,
                             topic_id: int) -> None:
    if state is None:
        await callback.answer("Недоступно", show_alert=True)
        return
    topic = await TopicRepository(session).get_by_id(topic_id)
    if topic is None:
        await callback.answer("Тема не найдена", show_alert=True)
        return
    await state.set_state(AdminStates.waiting_topic_events)
    await state.update_data(topic_id=topic_id)
    current = topic.event_types or "—"
    await callback.message.edit_text(
        f"Текущий тип событий: {html_escape(current)}\n"
        "Введите новый тип событий через запятую (например: tasks,reports) "
        "или «-» чтобы очистить:")
    await callback.answer()


@router.message(AdminStates.waiting_topic_events)
async def handle_topic_events_message(message: Message, session, state: FSMContext) -> None:
    actor = await _resolve_actor_message(message, session)
    if actor is None:
        await state.clear()
        return
    data = await state.get_data()
    topic_id = data.get("topic_id")
    topic = await TopicRepository(session).get_by_id(topic_id) if topic_id is not None else None
    if topic is None:
        await message.answer("Тема не найдена")
        await state.clear()
        return
    raw = (message.text or "").strip()
    new_value = None if raw in ("", "-") else ",".join(
        part.strip() for part in raw.split(",") if part.strip())
    old_value = topic.event_types
    topic.event_types = new_value
    await session.flush()
    await AuditService(session).log(actor.id, "topic.event_types_change", entity_type="topic",
                                    entity_id=topic.topic_key, old_value=old_value,
                                    new_value=new_value)
    await session.commit()
    await state.clear()
    await message.answer("Тип событий обновлён ✅")


# --------------------------------------------------------------------------
# Активация / деактивация (деактивация — ОПАСНАЯ операция, только через
# confirm_token, по аналогии с деактивацией пользователя Task 25)
# --------------------------------------------------------------------------

async def _toggle_active(callback: CallbackQuery, session, actor: User,
                         svc: AdminService, topic_id: int) -> None:
    topic = await TopicRepository(session).get_by_id(topic_id)
    if topic is None:
        await callback.answer("Тема не найдена", show_alert=True)
        return
    if not topic.is_active:
        topic.is_active = True
        await session.flush()
        await AuditService(session).log(actor.id, "topic.activate", entity_type="topic",
                                        entity_id=topic.topic_key)
        await session.commit()
        text = await render_topic_card(session, topic)
        await callback.message.edit_text(
            text, reply_markup=topic_card_keyboard(topic.id, True))
        await callback.answer("Активирована ✅")
        return

    async def op() -> None:
        t = await TopicRepository(session).get_by_id(topic_id)
        if t is not None:
            t.is_active = False
            await session.flush()
        await AuditService(session).log(actor.id, "topic.deactivate", entity_type="topic",
                                        entity_id=topic.topic_key)

    token = svc.confirm_token(f"topic.deactivate.{topic_id}", op,
                              required_permission="topics.manage", creator_actor_id=actor.id)
    await callback.message.edit_text(
        f"Деактивировать тему {html_escape(topic.topic_name)}? "
        "Маршрутизация сообщений в эту тему остановится, тема останется в списке.",
        reply_markup=confirm_keyboard(token))
    await callback.answer()


# --------------------------------------------------------------------------
# «📨 Тестовая отправка» вручную — тот же TopicService, без изменения
# message_thread_id.
# --------------------------------------------------------------------------

async def _manual_test(callback: CallbackQuery, session, actor: User, topic_id: int) -> None:
    topic = await TopicRepository(session).get_by_id(topic_id)
    if topic is None:
        await callback.answer("Тема не найдена", show_alert=True)
        return
    ok = await TopicService(session, callback.bot).send_test_message(topic.topic_key)
    _LAST_TEST_RESULT[topic.topic_key] = ok
    await AuditService(session).log(actor.id, "topic.test_send", entity_type="topic",
                                    entity_id=topic.topic_key, result="ok" if ok else "error")
    await session.commit()
    text = await render_topic_card(session, topic)
    await callback.message.edit_text(
        text, reply_markup=topic_card_keyboard(topic.id, topic.is_active))
    await callback.answer("Тестовое сообщение отправлено ✅" if ok else "Ошибка тестовой отправки ❌",
                          show_alert=not ok)


# --------------------------------------------------------------------------
# Точки входа
# --------------------------------------------------------------------------

async def handle_topics_section(callback: CallbackQuery, callback_data: AdminCb, session,
                                actor: User, svc: AdminService,
                                state: FSMContext | None = None) -> None:
    """Точка входа для AdminCb.s == "top" (пункт меню «🗂 Темы Telegram»).

    Право topics.manage уже проверено resolve_admin/handle_section до вызова.
    Вся дальнейшая навигация уходит на TopCb (см. handle_top_callback ниже),
    который проверяет право заново."""
    if callback_data.a == "menu":
        await _show_menu(callback, actor, svc)
    else:
        await _show_topics_list(callback, session, 1)


@router.callback_query(TopCb.filter())
async def handle_top_callback(callback: CallbackQuery, callback_data: TopCb, session,
                              state: FSMContext | None = None) -> None:
    actor, svc = await _resolve_actor(callback, session)
    if actor is None:
        return
    action = callback_data.a
    if action == "list":
        await _show_topics_list(callback, session, callback_data.p)
    elif action == "card":
        await _show_card(callback, session, callback_data.id)
    elif action == "editname":
        await _start_edit_name(callback, session, state, callback_data.id)
    elif action == "editthread":
        await _start_edit_thread(callback, session, state, callback_data.id)
    elif action == "editevents":
        await _start_edit_events(callback, session, state, callback_data.id)
    elif action == "toggle":
        await _toggle_active(callback, session, actor, svc, callback_data.id)
    elif action == "test":
        await _manual_test(callback, session, actor, callback_data.id)
    elif action == "noop":
        await callback.answer()
    else:
        await callback.answer("Неизвестное действие", show_alert=True)
