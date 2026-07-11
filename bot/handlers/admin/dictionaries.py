"""Раздел админ-панели «⚠ Категории проблем» / «🛠 Варианты решений»
(+ «📦 Категории товаров») (Task 30).

Три справочника (`article_categories`/`problem_types`/`decision_types`)
управляются ОДНИМ набором handler'ов через whitelist `DICT_MODELS` (ключ ->
модель + FK-колонка в `ArticleAction`, используемая для проверки, используется
ли запись в истории). Меню админ-панели даёт два входа — «dic» (Категории
проблем) и «dic2» (Варианты решений, алиасится на то же право
`dictionaries.manage`, см. `SECTION_ALIASES`/Task 23) — «Категории товаров»
отдельного пункта меню не имеет (в `MENU_ITEMS` такого пункта нет) и доступны
через переключатель `kind_switcher_row` на экране списка любого из трёх
справочников (см. `bot/keyboards/admin/dictionaries.py`).

Навигация: список (активные + неактивные с пометкой «(неактивна)», сортировка
sort_order) -> карточка (для problem_types/decision_types — доп. поля
require_comment/default_next_check_days) -> добавить (FSM) / переименовать
(FSM) / переставить (⬆/⬇, мгновенно) / деактивировать-восстановить (МГНОВЕННО,
без confirm_token — обратимая операция, брифовое явное решение) / удалить
(ТОЛЬКО если `can_delete`; кнопка отсутствует в клавиатуре для используемых
записей — Important-паттерн из Task 29: скрытая кнопка + сам handler ВСЁ РАВНО
перепроверяет `can_delete` на случай подделанного callback). Для problem_types
дополнительно «🔗 Рекомендуемые решения» — мультивыбор через `ProblemDecisionLink`.

Отклонение сверх брифа: `delete_entry` (буквальный код брифа) физически удаляет
строку БЕЗ подтверждения — в отличие от ЛЮБОЙ другой необратимой/деструктивной
операции в разделах Tasks 25-29 (везде используется `confirm_token`). Здесь
`_do_delete` добавляет `confirm_token` поверх брифового `delete_entry` — это
физическое удаление строго опаснее, чем деактивация (которая как раз явно
брифом освобождена от подтверждения как обратимая), и последовательность
"опасная необратимая операция = confirm_token" — установленный инвариант всего
раздела админ-панели с Task 23 (Critical-эксплойт, если её не соблюдать).

Как и в разделах Tasks 25-29, навигация использует СОБСТВЕННЫЙ `DicCb`
(prefix="d"), НЕ `AdminCb` — каждый callback этого модуля и каждое
FSM-продолжение сообщением заново проверяет actor + право `dictionaries.manage`.
"""
import json

from aiogram import Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import func, select

from bot.database.models import (
    ArticleAction, ArticleCategory, DecisionType, ProblemDecisionLink, ProblemType, User,
)
from bot.keyboards.admin.confirm import confirm_keyboard
from bot.keyboards.admin.dictionaries import (
    DicCb, KIND_TITLES, cancel_keyboard, dictionary_card_keyboard, dictionary_list_keyboard,
    links_keyboard, yes_no_keyboard,
)
from bot.keyboards.admin.main import AdminCb, admin_menu_keyboard
from bot.services.admin_service import AdminService
from bot.services.audit_service import AuditService
from bot.services.setting_service import SettingService
from bot.services.user_service import UserService
from bot.states.admin_states import AdminStates
from bot.utils.html_utils import html_escape
from bot.utils.validation import validate_int

router = Router(name=__name__)

# --------------------------------------------------------------------------
# Доменные функции (тестируются напрямую, без callback-обвязки) — сигнатуры и
# тела DICT_MODELS/_usage_count/can_delete/delete_entry/set_active/move_entry/
# toggle_link заданы брифом Task 30 дословно.
# --------------------------------------------------------------------------

DICT_MODELS = {
    "article_categories": (ArticleCategory, ArticleAction.category_id),
    "problem_types": (ProblemType, ArticleAction.problem_type_id),
    "decision_types": (DecisionType, ArticleAction.decision_type_id),
}

# Только эти два справочника имеют require_comment/default_next_check_days.
EXTENDED_FIELDS_KINDS = frozenset({"problem_types", "decision_types"})


async def _usage_count(session, dict_name: str, entry_id: int) -> int:
    _, fk = DICT_MODELS[dict_name]
    return await session.scalar(select(func.count()).where(fk == entry_id))


async def can_delete(session, dict_name: str, entry_id: int) -> bool:
    return await _usage_count(session, dict_name, entry_id) == 0


async def delete_entry(session, actor: User, dict_name: str, entry_id: int) -> None:
    if dict_name not in DICT_MODELS:
        raise KeyError("Неизвестный справочник")
    if not await can_delete(session, dict_name, entry_id):
        raise ValueError("Запись используется в истории — доступна только деактивация")
    model, _ = DICT_MODELS[dict_name]
    entry = await session.get(model, entry_id)
    if entry is not None:
        await session.delete(entry)
        await AuditService(session).log(actor.id, f"{dict_name}.delete",
                                        entity_type=dict_name, entity_id=str(entry_id),
                                        old_value=entry.name)


async def set_active(session, actor: User, dict_name: str, entry_id: int, active: bool) -> None:
    model, _ = DICT_MODELS[dict_name]
    entry = await session.get(model, entry_id)
    if entry is None:
        raise ValueError("Запись не найдена")
    entry.is_active = active
    await AuditService(session).log(
        actor.id, f"{dict_name}.{'restore' if active else 'deactivate'}",
        entity_type=dict_name, entity_id=str(entry_id), old_value=entry.name)


async def move_entry(session, actor: User, dict_name: str, entry_id: int, direction: str) -> None:
    model, _ = DICT_MODELS[dict_name]
    entries = list(await session.scalars(select(model).order_by(model.sort_order)))
    idx = next(i for i, e in enumerate(entries) if e.id == entry_id)
    target = idx + 1 if direction == "down" else idx - 1
    if not 0 <= target < len(entries):
        return
    entries[idx].sort_order, entries[target].sort_order = (
        entries[target].sort_order, entries[idx].sort_order)
    await session.flush()
    await AuditService(session).log(actor.id, f"{dict_name}.reorder",
                                    entity_type=dict_name, entity_id=str(entry_id))


async def toggle_link(session, actor: User, problem_type_id: int, decision_type_id: int) -> None:
    existing = await session.scalar(select(ProblemDecisionLink).where(
        ProblemDecisionLink.problem_type_id == problem_type_id,
        ProblemDecisionLink.decision_type_id == decision_type_id))
    if existing is not None:
        await session.delete(existing)
        action = "dictionaries.unlink"
    else:
        session.add(ProblemDecisionLink(problem_type_id=problem_type_id,
                                        decision_type_id=decision_type_id))
        action = "dictionaries.link"
    await session.flush()
    await AuditService(session).log(actor.id, action, entity_type="problem_decision_link",
                                    entity_id=f"{problem_type_id}:{decision_type_id}")


async def rename_entry(session, actor: User, dict_name: str, entry_id: int, name: str) -> tuple[bool, str]:
    model, _ = DICT_MODELS[dict_name]
    entry = await session.get(model, entry_id)
    if entry is None:
        return False, "Запись не найдена"
    if not name.strip():
        return False, "Название не может быть пустым"
    old = entry.name
    entry.name = name.strip()
    await session.flush()
    await AuditService(session).log(actor.id, f"{dict_name}.rename",
                                    entity_type=dict_name, entity_id=str(entry_id),
                                    old_value=old, new_value=entry.name)
    return True, "Переименовано ✅"


async def create_entry(session, actor: User, dict_name: str, name: str,
                       require_comment: bool = False,
                       default_next_check_days: int | None = None) -> object:
    model, _ = DICT_MODELS[dict_name]
    existing = list(await session.scalars(select(model)))
    sort_order = (max((e.sort_order for e in existing), default=-1)) + 1
    kwargs = dict(name=name.strip(), sort_order=sort_order, is_active=True)
    if dict_name in EXTENDED_FIELDS_KINDS:
        kwargs["require_comment"] = require_comment
        kwargs["default_next_check_days"] = default_next_check_days
    entry = model(**kwargs)
    session.add(entry)
    await session.flush()
    await AuditService(session).log(actor.id, f"{dict_name}.create",
                                    entity_type=dict_name, entity_id=str(entry.id),
                                    new_value=entry.name)
    return entry


# --------------------------------------------------------------------------
# Отображение
# --------------------------------------------------------------------------

def render_entry_card(dict_name: str, entry) -> str:
    lines = [
        f"{KIND_TITLES[dict_name]}",
        f"Название: {html_escape(entry.name)}",
        f"Порядок сортировки: {entry.sort_order}",
        f"Статус: {'активна' if entry.is_active else 'неактивна'}",
    ]
    if dict_name == "decision_types":
        # Бриф явно требует показывать это поле на карточке решения (task-30-brief.md:10).
        # Мастер добавления его не заполняет (в брифе нет описания UX для выбора
        # категорий) — сознательно оставлено редактируемым только напрямую в БД/
        # будущей задачей; здесь — только отображение уже имеющегося значения.
        raw = entry.allowed_categories_json
        if raw:
            try:
                ids = json.loads(raw)
                shown = ", ".join(str(i) for i in ids) if ids else "—"
            except (TypeError, ValueError):
                shown = "—"
        else:
            shown = "—"
        lines.append(f"Допустимые категории (id): {shown}")
    if dict_name in EXTENDED_FIELDS_KINDS:
        lines.append(f"Обязательный комментарий: {'да' if entry.require_comment else 'нет'}")
        days = entry.default_next_check_days
        lines.append(f"Срок следующей проверки по умолчанию (дней): {days if days is not None else '—'}")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# actor-check (каждый DicCb-callback и каждое FSM-сообщение проверяют заново —
# resolve_admin из main.py срабатывает только для AdminCb, не для DicCb)
# --------------------------------------------------------------------------

async def _resolve_actor(callback: CallbackQuery, session) -> tuple[User | None, AdminService | None]:
    actor = await UserService(session).get_actor(callback.from_user.id)
    svc = AdminService(session)
    if actor is None or not await svc.permissions.has_permission(actor, "dictionaries.manage"):
        await callback.answer("Недостаточно прав", show_alert=True)
        return None, None
    return actor, svc


async def _resolve_actor_message(message: Message, session) -> User | None:
    actor = await UserService(session).get_actor(message.from_user.id)
    svc = AdminService(session)
    if actor is None or not await svc.permissions.has_permission(actor, "dictionaries.manage"):
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


async def _show_list(callback: CallbackQuery, session, kind: str, page: int) -> None:
    model, _ = DICT_MODELS[kind]
    entries = list(await session.scalars(select(model).order_by(model.sort_order)))
    settings_svc = SettingService(session)
    page_size = max(1, int(await settings_svc.get("general.page_size")))
    total_pages = max(1, -(-len(entries) // page_size))
    page = min(max(1, page), total_pages)
    start = (page - 1) * page_size
    rows = [(e.id, e.name if e.is_active else f"{e.name} (неактивна)")
           for e in entries[start:start + page_size]]
    await callback.message.edit_text(
        KIND_TITLES[kind], reply_markup=dictionary_list_keyboard(kind, rows, page, total_pages))
    await callback.answer()


async def _show_card(callback: CallbackQuery, session, kind: str, entry_id: int) -> None:
    model, _ = DICT_MODELS[kind]
    entry = await session.get(model, entry_id)
    if entry is None:
        await callback.answer("Запись не найдена", show_alert=True)
        return
    show_delete = await can_delete(session, kind, entry_id)
    show_links = kind == "problem_types"
    await callback.message.edit_text(
        render_entry_card(kind, entry),
        reply_markup=dictionary_card_keyboard(kind, entry.id, entry.is_active, show_delete, show_links))
    await callback.answer()


# --------------------------------------------------------------------------
# Переименование (FSM)
# --------------------------------------------------------------------------

async def _start_rename(callback: CallbackQuery, session, state: FSMContext | None,
                        kind: str, entry_id: int) -> None:
    model, _ = DICT_MODELS[kind]
    entry = await session.get(model, entry_id)
    if entry is None:
        await callback.answer("Запись не найдена", show_alert=True)
        return
    if state is None:
        await callback.answer("Недоступно", show_alert=True)
        return
    await state.set_state(AdminStates.waiting_dic_rename)
    await state.update_data(kind=kind, entry_id=entry_id)
    await callback.message.edit_text(
        f"Текущее название: {html_escape(entry.name)}\nВведите новое название:",
        reply_markup=cancel_keyboard(kind))
    await callback.answer()


@router.message(AdminStates.waiting_dic_rename)
async def handle_dic_rename_message(message: Message, session, state: FSMContext) -> None:
    actor = await _resolve_actor_message(message, session)
    if actor is None:
        await state.clear()
        return
    data = await state.get_data()
    kind = data.get("kind")
    entry_id = data.get("entry_id")
    if kind is None or entry_id is None:
        await message.answer("Сессия редактирования утеряна, начните заново")
        await state.clear()
        return
    ok, msg = await rename_entry(session, actor, kind, entry_id, message.text or "")
    await session.commit()
    await message.answer(msg)
    if ok:
        await state.clear()


# --------------------------------------------------------------------------
# Добавление (FSM: name -> [require_comment -> default_next_check_days])
# --------------------------------------------------------------------------

async def _start_add(callback: CallbackQuery, state: FSMContext | None, kind: str) -> None:
    if state is None:
        await callback.answer("Недоступно", show_alert=True)
        return
    await state.set_state(AdminStates.waiting_dic_add)
    await state.update_data(kind=kind, step="name")
    await callback.message.edit_text(
        f"Добавление записи: {KIND_TITLES[kind]}\nВведите название:",
        reply_markup=cancel_keyboard(kind))
    await callback.answer()


async def _finish_add_simple(session, actor: User, kind: str, name: str):
    return await create_entry(session, actor, kind, name)


@router.message(AdminStates.waiting_dic_add)
async def handle_dic_add_message(message: Message, session, state: FSMContext) -> None:
    actor = await _resolve_actor_message(message, session)
    if actor is None:
        await state.clear()
        return
    data = await state.get_data()
    kind = data.get("kind")
    step = data.get("step")
    raw = (message.text or "").strip()

    if step == "name":
        if not raw:
            await message.answer("Название не может быть пустым, введите ещё раз:")
            return
        if kind not in EXTENDED_FIELDS_KINDS:
            entry = await _finish_add_simple(session, actor, kind, raw)
            await session.commit()
            await state.clear()
            await message.answer(render_entry_card(kind, entry),
                                 reply_markup=dictionary_card_keyboard(
                                     kind, entry.id, True, await can_delete(session, kind, entry.id),
                                     kind == "problem_types"))
            return
        await state.update_data(name=raw, step="require_comment")
        await message.answer("Обязателен ли комментарий при выборе этого варианта?",
                             reply_markup=yes_no_keyboard(kind, "add_rc"))
        return

    if step == "default_next_check_days":
        try:
            days = None if raw in ("", "-") else validate_int(raw, 0)
        except ValueError as exc:
            await message.answer(str(exc))
            return
        entry = await create_entry(session, actor, kind, data["name"],
                                   require_comment=data.get("require_comment", False),
                                   default_next_check_days=days)
        await session.commit()
        await state.clear()
        await message.answer(render_entry_card(kind, entry),
                             reply_markup=dictionary_card_keyboard(
                                 kind, entry.id, True, await can_delete(session, kind, entry.id),
                                 kind == "problem_types"))
        return

    await message.answer("Сейчас нужно выбрать вариант кнопкой, а не текстом.")


async def _pick_require_comment(callback: CallbackQuery, state: FSMContext | None,
                                kind: str, value: int) -> None:
    if state is None:
        await callback.answer("Недоступно", show_alert=True)
        return
    data = await state.get_data()
    if data.get("step") != "require_comment":
        await callback.answer("Некорректный шаг мастера", show_alert=True)
        return
    await state.update_data(require_comment=bool(value), step="default_next_check_days")
    await callback.message.edit_text(
        "Через сколько дней по умолчанию следующая проверка? (число, или «-», чтобы не задавать)",
        reply_markup=cancel_keyboard(kind))
    await callback.answer()


# --------------------------------------------------------------------------
# Переставить / деактивировать-восстановить (мгновенно) / удалить (confirm_token)
# --------------------------------------------------------------------------

async def _do_move(callback: CallbackQuery, session, actor: User, kind: str,
                   entry_id: int, direction: str) -> None:
    await move_entry(session, actor, kind, entry_id, direction)
    await session.commit()
    await _show_card(callback, session, kind, entry_id)


async def _do_toggle(callback: CallbackQuery, session, actor: User, kind: str, entry_id: int) -> None:
    model, _ = DICT_MODELS[kind]
    entry = await session.get(model, entry_id)
    if entry is None:
        await callback.answer("Запись не найдена", show_alert=True)
        return
    try:
        await set_active(session, actor, kind, entry_id, not entry.is_active)
    except ValueError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await session.commit()
    await _show_card(callback, session, kind, entry_id)


async def _do_delete(callback: CallbackQuery, session, actor: User, svc: AdminService,
                     kind: str, entry_id: int) -> None:
    model, _ = DICT_MODELS[kind]
    entry = await session.get(model, entry_id)
    if entry is None:
        await callback.answer("Запись не найдена", show_alert=True)
        return
    if not await can_delete(session, kind, entry_id):
        await callback.answer("Запись используется в истории — доступна только деактивация",
                              show_alert=True)
        return
    entry_name = entry.name    # уже загруженный скаляр — безопасен и после закрытия
                                # внешней сессии (expire_on_commit=False)

    async def op(session) -> None:
        # `session` — параметр (сессия ПОДТВЕРЖДАЮЩЕГО запроса), НЕ внешняя
        # переменная того же имени из _do_delete — см. docstring
        # AdminService.confirm_token (Task 27 review fix, Critical).
        try:
            await delete_entry(session, actor, kind, entry_id)
        except ValueError:
            pass    # запись успела попасть в использование между показом кнопки и подтверждением

    token = svc.confirm_token(f"{kind}.delete.{entry_id}", op,
                              required_permission="dictionaries.manage", creator_actor_id=actor.id)
    await callback.message.edit_text(
        f"Удалить «{html_escape(entry_name)}» безвозвратно? Действие нельзя отменить.",
        reply_markup=confirm_keyboard(token))
    await callback.answer()


# --------------------------------------------------------------------------
# Рекомендуемые решения (только problem_types)
# --------------------------------------------------------------------------

async def _show_links(callback: CallbackQuery, session, problem_id: int) -> None:
    problem = await session.get(ProblemType, problem_id)
    if problem is None:
        await callback.answer("Запись не найдена", show_alert=True)
        return
    decisions = list(await session.scalars(
        select(DecisionType).where(DecisionType.is_active.is_(True)).order_by(DecisionType.sort_order)))
    linked_ids = set(await session.scalars(
        select(ProblemDecisionLink.decision_type_id).where(
            ProblemDecisionLink.problem_type_id == problem_id)))
    entries = [(d.id, d.name, d.id in linked_ids) for d in decisions]
    await callback.message.edit_text(
        f"🔗 Рекомендуемые решения для «{html_escape(problem.name)}»",
        reply_markup=links_keyboard(problem_id, entries))
    await callback.answer()


async def _do_toggle_link(callback: CallbackQuery, session, actor: User,
                          problem_id: int, decision_id: int) -> None:
    await toggle_link(session, actor, problem_id, decision_id)
    await session.commit()
    await _show_links(callback, session, problem_id)


# --------------------------------------------------------------------------
# Точки входа
# --------------------------------------------------------------------------

async def handle_dictionaries_section(callback: CallbackQuery, callback_data: AdminCb, session,
                                      actor: User, svc: AdminService,
                                      state: FSMContext | None = None) -> None:
    """Точка входа для AdminCb.s in ("dic", "dic2") (пункты меню «⚠ Категории
    проблем» и «🛠 Варианты решений» — оба ведут сюда, см. SECTION_HANDLERS).

    Право dictionaries.manage уже проверено resolve_admin/handle_section до
    вызова. Вся дальнейшая навигация уходит на DicCb (см. handle_dic_callback
    ниже), который проверяет право заново."""
    if callback_data.a == "menu":
        await _show_menu(callback, actor, svc)
        return
    kind = "decision_types" if callback_data.s == "dic2" else "problem_types"
    await _show_list(callback, session, kind, 1)


@router.callback_query(DicCb.filter())
async def handle_dic_callback(callback: CallbackQuery, callback_data: DicCb, session,
                              state: FSMContext | None = None) -> None:
    actor, svc = await _resolve_actor(callback, session)
    if actor is None:
        return
    action = callback_data.a
    kind = callback_data.kind
    # Навигационные действия покидают контекст редактирования/добавления —
    # обязаны сбросить FSM (урок Task 28: без сброса следующий текст молча
    # применился бы как значение).
    if action in ("list", "card") and state is not None:
        await state.clear()
    if action == "list":
        await _show_list(callback, session, kind, callback_data.p)
    elif action == "card":
        await _show_card(callback, session, kind, callback_data.id)
    elif action == "add":
        await _start_add(callback, state, kind)
    elif action == "add_rc":
        await _pick_require_comment(callback, state, kind, callback_data.id)
    elif action == "rename":
        await _start_rename(callback, session, state, kind, callback_data.id)
    elif action in ("up", "down"):
        await _do_move(callback, session, actor, kind, callback_data.id, action)
    elif action == "toggle":
        await _do_toggle(callback, session, actor, kind, callback_data.id)
    elif action == "delete":
        await _do_delete(callback, session, actor, svc, kind, callback_data.id)
    elif action == "links":
        await _show_links(callback, session, callback_data.id)
    elif action == "link":
        await _do_toggle_link(callback, session, actor, callback_data.id, callback_data.id2)
    elif action == "noop":
        await callback.answer()
    else:
        await callback.answer("Неизвестное действие", show_alert=True)
