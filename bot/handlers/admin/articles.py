"""Раздел админ-панели «📦 Артикулы» (Task 29).

Навигация: список артикулов (пагинация `general.page_size`, фильтр архивных
`article_check.include_archived` — при `false` неактивные скрыты из списка
целиком, при `true` показаны с пометкой «(неактивен)») -> карточка (артикул,
название, sort_order, ответственный, источник, активность) -> редактирование
названия/sort_order/ответственного (FSM), активировать/деактивировать
(деактивация — ОПАСНАЯ операция, только через `AdminService.confirm_token`, по
аналогии Tasks 25-28), «➕ Добавить вручную» (кнопка рендерится ТОЛЬКО при
`article_check.allow_manual_article_add=true` — см. `articles_list_keyboard`;
`add_manual_article` дополнительно перепроверяет настройку сама, т.к. кнопка
может быть скрыта в UI, но прямой вызов callback'а всё равно обязан быть
безопасным).

Как и в разделах Tasks 25-28, навигация использует СОБСТВЕННЫЙ `ArtCb`
(prefix="ar"), НЕ `AdminCb` — каждый callback этого модуля и каждое
FSM-продолжение сообщением заново проверяет actor + право `articles.manage`.

`ArticleRepository.get_all(include_inactive)` добавлен этой задачей (Task 29)
по аналогии с `UserRepository.get_all`/`TopicRepository.get_all`/
`TaskRepository.get_all_configs` — раньше в `ArticleRepository` был только
`get_active()`, недостаточный для списка с фильтром архивных.
"""
from aiogram import Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.database.models import Article, User
from bot.database.repositories.article_repository import ArticleRepository
from bot.database.repositories.user_repository import UserRepository
from bot.keyboards.admin.articles import (
    ArtCb, article_card_keyboard, articles_list_keyboard, cancel_add_keyboard,
    cancel_edit_keyboard,
)
from bot.keyboards.admin.confirm import confirm_keyboard
from bot.keyboards.admin.main import AdminCb, admin_menu_keyboard
from bot.services.admin_service import AdminService
from bot.services.audit_service import AuditService
from bot.services.setting_service import SettingService
from bot.services.user_service import UserService
from bot.states.admin_states import AdminStates
from bot.utils.html_utils import code, html_escape
from bot.utils.validation import validate_int

router = Router(name=__name__)

# --------------------------------------------------------------------------
# Доменные функции (тестируются напрямую, без callback-обвязки) — сигнатура и
# тело add_manual_article заданы брифом Task 29 дословно.
# --------------------------------------------------------------------------

EDITABLE_FIELDS = frozenset({"product_name", "sort_order", "responsible_user_id"})

FIELD_TITLES: dict[str, str] = {
    "product_name": "Название",
    "sort_order": "Порядок сортировки",
    "responsible_user_id": "Ответственный (user_id)",
}


async def add_manual_article(session, actor: User, article: str,
                             product_name: str | None) -> Article:
    if not bool(await SettingService(session).get("article_check.allow_manual_article_add")):
        raise PermissionError("Ручное добавление артикулов отключено настройкой")
    repo = ArticleRepository(session)
    if await repo.get_by_article(article) is not None:
        raise ValueError("Артикул уже существует")
    created = await repo.upsert(article=article, product_name=product_name,
                                sort_order=0, is_active=True, source="manual")
    await AuditService(session).log(actor.id, "article.add_manual",
                                    entity_type="article", entity_id=article)
    return created


async def apply_field_edit(session, actor: User, article_id: int, field: str,
                           value) -> tuple[bool, str]:
    if field not in EDITABLE_FIELDS:
        return False, "Поле запрещено к редактированию"
    row = await session.get(Article, article_id)
    if row is None:
        return False, "Артикул не найден"
    old = getattr(row, field)
    setattr(row, field, value)             # безопасно: field уже прошёл whitelist EDITABLE_FIELDS
    await session.flush()
    await AuditService(session).log(actor.id, "article.edit",
                                    entity_type="article", entity_id=row.article,
                                    setting_key=field, old_value=str(old), new_value=str(value))
    return True, "Сохранено ✅"


# --------------------------------------------------------------------------
# Отображение
# --------------------------------------------------------------------------

async def render_article_card(session, art: Article) -> str:
    responsible = "—"
    if art.responsible_user_id:
        user = await UserRepository(session).get_by_id(art.responsible_user_id)
        responsible = user.name if user else str(art.responsible_user_id)
    lines = [
        f"📦 Артикул: {code(art.article)}",
        f"Название: {html_escape(art.product_name) if art.product_name else '—'}",
        f"Порядок сортировки: {art.sort_order}",
        f"Ответственный: {html_escape(responsible)}",
        f"Источник: {html_escape(art.source)}",
        f"Статус: {'активен' if art.is_active else 'неактивен'}",
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------
# actor-check (каждый ArtCb-callback и каждое FSM-сообщение проверяют заново —
# resolve_admin из main.py срабатывает только для AdminCb, не для ArtCb)
# --------------------------------------------------------------------------

async def _resolve_actor(callback: CallbackQuery, session) -> tuple[User | None, AdminService | None]:
    actor = await UserService(session).get_actor(callback.from_user.id)
    svc = AdminService(session)
    if actor is None or not await svc.permissions.has_permission(actor, "articles.manage"):
        await callback.answer("Недостаточно прав", show_alert=True)
        return None, None
    return actor, svc


async def _resolve_actor_message(message: Message, session) -> User | None:
    actor = await UserService(session).get_actor(message.from_user.id)
    svc = AdminService(session)
    if actor is None or not await svc.permissions.has_permission(actor, "articles.manage"):
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


async def _show_articles_list(callback: CallbackQuery, session, page: int) -> None:
    settings_svc = SettingService(session)
    include_archived = bool(await settings_svc.get("article_check.include_archived"))
    allow_add = bool(await settings_svc.get("article_check.allow_manual_article_add"))
    articles = sorted(await ArticleRepository(session).get_all(include_inactive=include_archived),
                      key=lambda a: (a.sort_order, a.article))
    page_size = max(1, int(await settings_svc.get("general.page_size")))
    total_pages = max(1, -(-len(articles) // page_size))
    page = min(max(1, page), total_pages)
    start = (page - 1) * page_size
    entries = [(a.id, a.article if a.is_active else f"{a.article} (неактивен)")
               for a in articles[start:start + page_size]]
    await callback.message.edit_text(
        "📦 Артикулы", reply_markup=articles_list_keyboard(entries, page, total_pages, allow_add))
    await callback.answer()


async def _show_card(callback: CallbackQuery, session, article_id: int) -> None:
    art = await session.get(Article, article_id)
    if art is None:
        await callback.answer("Артикул не найден", show_alert=True)
        return
    text = await render_article_card(session, art)
    await callback.message.edit_text(text, reply_markup=article_card_keyboard(art.id, art.is_active))
    await callback.answer()


# --------------------------------------------------------------------------
# Редактирование одного поля
# --------------------------------------------------------------------------

async def _start_edit_field(callback: CallbackQuery, state: FSMContext | None, session,
                            article_id: int, field: str) -> None:
    art = await session.get(Article, article_id)
    if art is None:
        await callback.answer("Артикул не найден", show_alert=True)
        return
    if state is None:
        await callback.answer("Недоступно", show_alert=True)
        return
    current = getattr(art, field)
    await state.set_state(AdminStates.waiting_art_edit)
    await state.update_data(article_id=article_id, field=field)
    hint = ("Введите ID пользователя (число) или «-», чтобы очистить:"
           if field == "responsible_user_id" else
           "Введите целое число (порядок сортировки):" if field == "sort_order" else
           "Введите новое название (или «-», чтобы очистить):")
    await callback.message.edit_text(
        f"Поле: {html_escape(FIELD_TITLES[field])}\n"
        f"Текущее значение: {current if current is not None else '—'}\n{hint}",
        reply_markup=cancel_edit_keyboard(article_id))
    await callback.answer()


async def _parse_field_raw(session, field: str, raw: str) -> object:
    raw = raw.strip()
    empty = raw in ("", "-")
    if field == "product_name":
        return None if empty else raw
    if field == "sort_order":
        return validate_int(raw)
    if field == "responsible_user_id":
        if empty:
            return None
        user_id = validate_int(raw)
        user = await UserRepository(session).get_by_id(user_id)
        if user is None or not user.is_active:
            raise ValueError("Пользователь должен существовать и быть активным")
        return user_id
    raise ValueError("Поле недоступно для редактирования")


@router.message(AdminStates.waiting_art_edit)
async def handle_art_edit_message(message: Message, session, state: FSMContext) -> None:
    actor = await _resolve_actor_message(message, session)
    if actor is None:
        await state.clear()
        return
    data = await state.get_data()
    article_id = data.get("article_id")
    field = data.get("field")
    if article_id is None or field is None:
        await message.answer("Сессия редактирования утеряна, начните заново")
        await state.clear()
        return
    try:
        value = await _parse_field_raw(session, field, message.text or "")
    except ValueError as exc:
        await message.answer(str(exc))
        return
    ok, msg = await apply_field_edit(session, actor, article_id, field, value)
    await session.commit()
    await message.answer(msg)
    if ok:
        await state.clear()


# --------------------------------------------------------------------------
# Активация / деактивация (деактивация — ОПАСНАЯ операция, только через
# confirm_token, по аналогии Tasks 25-28)
# --------------------------------------------------------------------------

async def _toggle_active(callback: CallbackQuery, session, actor: User, svc: AdminService,
                         article_id: int) -> None:
    art = await session.get(Article, article_id)
    if art is None:
        await callback.answer("Артикул не найден", show_alert=True)
        return
    if not art.is_active:
        art.is_active = True
        await session.flush()
        await AuditService(session).log(actor.id, "article.activate",
                                        entity_type="article", entity_id=art.article)
        await session.commit()
        text = await render_article_card(session, art)
        await callback.message.edit_text(text, reply_markup=article_card_keyboard(art.id, True))
        await callback.answer("Активирован ✅")
        return

    article_code = art.article           # уже загруженный скаляр — безопасен и после
                                          # закрытия внешней сессии (expire_on_commit=False)

    async def op(session) -> None:
        # `session` — параметр (сессия ПОДТВЕРЖДАЮЩЕГО запроса), НЕ внешняя
        # переменная того же имени из _toggle_active — см. docstring
        # AdminService.confirm_token (Task 27 review fix, Critical).
        a = await session.get(Article, article_id)
        if a is not None:
            a.is_active = False
            await session.flush()
        await AuditService(session).log(actor.id, "article.deactivate",
                                        entity_type="article", entity_id=article_code)

    token = svc.confirm_token(f"article.deactivate.{article_id}", op,
                              required_permission="articles.manage", creator_actor_id=actor.id)
    await callback.message.edit_text(
        f"Деактивировать артикул {html_escape(article_code)}? "
        "Он не будет включаться в новые пакетные проверки, но останется в истории.",
        reply_markup=confirm_keyboard(token))
    await callback.answer()


# --------------------------------------------------------------------------
# «➕ Добавить вручную» — мастер FSM (article -> product_name)
# --------------------------------------------------------------------------

async def _start_add(callback: CallbackQuery, session, state: FSMContext | None) -> None:
    if not bool(await SettingService(session).get("article_check.allow_manual_article_add")):
        await callback.answer("Ручное добавление отключено настройкой", show_alert=True)
        return
    if state is None:
        await callback.answer("Недоступно", show_alert=True)
        return
    await state.set_state(AdminStates.waiting_art_add)
    await state.update_data(step="article")
    await callback.message.edit_text(
        "Добавление артикула вручную.\nВведите артикул:", reply_markup=cancel_add_keyboard())
    await callback.answer()


@router.message(AdminStates.waiting_art_add)
async def handle_art_add_message(message: Message, session, state: FSMContext) -> None:
    actor = await _resolve_actor_message(message, session)
    if actor is None:
        await state.clear()
        return
    data = await state.get_data()
    step = data.get("step")
    raw = (message.text or "").strip()

    if step == "article":
        if not raw:
            await message.answer("Артикул не может быть пустым, введите ещё раз:")
            return
        await state.update_data(article=raw, step="product_name")
        await message.answer("Введите название товара (или «-», чтобы пропустить):",
                             reply_markup=cancel_add_keyboard())
        return

    if step == "product_name":
        product_name = None if raw in ("", "-") else raw
        article_code = data.get("article")
        try:
            art = await add_manual_article(session, actor, article_code, product_name)
        except (PermissionError, ValueError) as exc:
            await message.answer(str(exc))
            await state.clear()
            return
        await session.commit()
        await state.clear()
        text = await render_article_card(session, art)
        await message.answer(text, reply_markup=article_card_keyboard(art.id, art.is_active))
        return

    await message.answer("Сейчас нужно ввести значение текстом.")


# --------------------------------------------------------------------------
# Точки входа
# --------------------------------------------------------------------------

async def handle_articles_section(callback: CallbackQuery, callback_data: AdminCb, session,
                                  actor: User, svc: AdminService,
                                  state: FSMContext | None = None) -> None:
    """Точка входа для AdminCb.s == "art" (пункт меню «📦 Артикулы»).

    Право articles.manage уже проверено resolve_admin/handle_section до
    вызова. Вся дальнейшая навигация уходит на ArtCb (см. handle_art_callback
    ниже), который проверяет право заново."""
    if callback_data.a == "menu":
        await _show_menu(callback, actor, svc)
    else:
        await _show_articles_list(callback, session, 1)


@router.callback_query(ArtCb.filter())
async def handle_art_callback(callback: CallbackQuery, callback_data: ArtCb, session,
                              state: FSMContext | None = None) -> None:
    actor, svc = await _resolve_actor(callback, session)
    if actor is None:
        return
    action = callback_data.a
    # Навигационные действия покидают контекст редактирования одного поля/
    # добавления — обязаны сбросить FSM (найдено ревью Task 28, тот же класс
    # бага: без сброса следующий текст молча применился бы как значение).
    if action in ("list", "card") and state is not None:
        await state.clear()
    if action == "list":
        await _show_articles_list(callback, session, callback_data.p)
    elif action == "card":
        await _show_card(callback, session, callback_data.id)
    elif action == "editname":
        await _start_edit_field(callback, state, session, callback_data.id, "product_name")
    elif action == "editorder":
        await _start_edit_field(callback, state, session, callback_data.id, "sort_order")
    elif action == "editresp":
        await _start_edit_field(callback, state, session, callback_data.id, "responsible_user_id")
    elif action == "toggle":
        await _toggle_active(callback, session, actor, svc, callback_data.id)
    elif action == "add":
        await _start_add(callback, session, state)
    elif action == "noop":
        await callback.answer()
    else:
        await callback.answer("Неизвестное действие", show_alert=True)
