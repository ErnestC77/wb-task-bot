from datetime import date, datetime, timedelta

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message,
)
from sqlalchemy import select

from bot.database.models import (
    ArticleCategory, ArticleCheckSession, CheckStatus, DecisionType, ProblemDecisionLink,
    ProblemType,
)
from bot.database.repositories.article_check_repository import ArticleCheckRepository
from bot.keyboards.article_check_keyboards import (
    ActCb, ChkCb, batch_keyboard, dict_keyboard, item_status_keyboard, render_batch,
)
from bot.keyboards.task_keyboards import TaskCb
from bot.services.article_check_service import ArticleCheckService
from bot.services.setting_service import SettingService
from bot.services.user_service import UserService
from bot.states.article_check_states import ArticleActionStates

router = Router(name=__name__)

_MARK_MAP = {"ok": CheckStatus.CHECKED_NO_ACTION,
             "act": CheckStatus.ACTION_REQUIRED,
             "q": CheckStatus.QUESTION}


async def _render(callback: CallbackQuery, svc: ArticleCheckService,
                  settings: SettingService, session_id: int, batch: int) -> None:
    view = await svc.get_batch_view(session_id, batch)
    show_names = bool(await settings.get("article_check.show_product_name"))
    allow_prev = bool(await settings.get("article_check.allow_prev_batch"))
    await callback.message.edit_text(render_batch(view, show_names),
                                     reply_markup=batch_keyboard(view, allow_prev))


@router.callback_query(TaskCb.filter(F.a == "start"))
async def handle_start_check(callback: CallbackQuery, callback_data: TaskCb, session):
    actor = await UserService(session).get_actor(callback.from_user.id)
    svc = ArticleCheckService(session, callback.bot)
    inst = await svc.tasks.get_instance(callback_data.i)
    if actor is None or inst is None:
        await callback.answer("Недоступно", show_alert=True)
        return
    try:
        s = await svc.start_check(inst, actor)
    except PermissionError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    view = await svc.get_batch_view(s.id, s.current_batch)
    settings = SettingService(session)
    show_names = bool(await settings.get("article_check.show_product_name"))
    allow_prev = bool(await settings.get("article_check.allow_prev_batch"))
    await callback.message.answer(render_batch(view, show_names),
                                  reply_markup=batch_keyboard(view, allow_prev))
    await session.commit()
    await callback.answer()


@router.callback_query(ChkCb.filter(F.a == "open"))
async def handle_open_item(callback: CallbackQuery, callback_data: ChkCb, session):
    actor = await UserService(session).get_actor(callback.from_user.id)
    svc = ArticleCheckService(session, callback.bot)
    item = await svc.repo.get_item(callback_data.it)
    if item is None or item.check_session_id != callback_data.s:
        await callback.answer("Артикул не найден", show_alert=True)   # поддельный ID
        return
    from bot.database.models import ArticleCheckSession
    chk_session = await session.get(ArticleCheckSession, item.check_session_id)
    if actor is None or chk_session is None or chk_session.responsible_user_id != actor.id:
        await callback.answer("Недостаточно прав", show_alert=True)
        return
    from bot.utils.html_utils import html_escape
    text = (f"Артикул: <code>{html_escape(item.article_snapshot)}</code>\n"
            f"Товар: {html_escape(item.product_name_snapshot or '—')}\n"
            f"Выберите результат проверки:")
    await callback.message.edit_text(text,
                                     reply_markup=item_status_keyboard(item, callback_data.s))
    await callback.answer()


@router.callback_query(ChkCb.filter(F.a == "mark"))
async def handle_mark(callback: CallbackQuery, callback_data: ChkCb, session,
                      state: FSMContext | None = None):
    actor = await UserService(session).get_actor(callback.from_user.id)
    if actor is None:
        await callback.answer("Недостаточно прав", show_alert=True)
        return
    svc = ArticleCheckService(session, callback.bot)
    try:
        ok = await svc.mark(callback_data.it, callback_data.v,
                            _MARK_MAP[callback_data.st], actor)
    except (PermissionError, ValueError) as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    if not ok:
        await callback.answer("Данные устарели, обновляю…", show_alert=False)
    if callback_data.st == "q":
        # запуск FSM вопроса по артикулу — Task 19. state прокидывается явно
        # (как и для "act" ниже) — без него FSM не сохранится между этим
        # callback'ом и следующим текстовым сообщением пользователя.
        from bot.handlers.questions import start_article_question
        await start_article_question(callback, callback_data.it, session, state)
        return
    if callback_data.st == "act":
        # запуск FSM фиксации действия. svc.mark() выше уже проверил, что actor —
        # ответственный за сессию проверки (иначе PermissionError), поэтому здесь
        # авторизация уже подтверждена; state прокидывается, чтобы шаги FSM ниже
        # реально сохранялись между callback'ами (без этого FSM работать не может).
        await start_action_fsm(callback, callback_data.it, session, state)
        return
    from bot.database.models import ArticleCheckSession
    item = await svc.repo.get_item(callback_data.it)
    s = await svc.session.get(ArticleCheckSession, item.check_session_id)
    await _render(callback, svc, SettingService(session), item.check_session_id, s.current_batch)
    await session.commit()
    await callback.answer()


@router.callback_query(ChkCb.filter(F.a == "nav"))
async def handle_nav(callback: CallbackQuery, callback_data: ChkCb, session):
    actor = await UserService(session).get_actor(callback.from_user.id)
    svc = ArticleCheckService(session, callback.bot)
    settings = SettingService(session)
    from bot.database.models import ArticleCheckSession
    s = await session.get(ArticleCheckSession, callback_data.s)
    if s is None or actor is None:
        await callback.answer("Недоступно", show_alert=True)
        return
    if s.responsible_user_id != actor.id:
        await callback.answer("Недостаточно прав", show_alert=True)
        return
    batch = callback_data.b or s.current_batch
    if callback_data.b:
        if batch < s.current_batch and not bool(
                await settings.get("article_check.allow_prev_batch")):
            await callback.answer(
                "Переход к предыдущей пачке запрещён настройкой", show_alert=True)
            return
        await svc.repo.set_current_batch(s.id, batch)
    await _render(callback, svc, settings, s.id, batch)
    await session.commit()
    await callback.answer()


@router.callback_query(ChkCb.filter(F.a == "fin"))
async def handle_finish_batch(callback: CallbackQuery, callback_data: ChkCb, session):
    actor = await UserService(session).get_actor(callback.from_user.id)
    svc = ArticleCheckService(session, callback.bot)
    from bot.database.models import ArticleCheckSession
    s = await session.get(ArticleCheckSession, callback_data.s)
    if s is None or actor is None or s.responsible_user_id != actor.id:
        await callback.answer("Недостаточно прав", show_alert=True)
        return
    if not await svc.can_finish_batch(callback_data.s, callback_data.b):
        await callback.answer("В пачке остались непроверенные артикулы", show_alert=True)
        return
    view = await svc.get_batch_view(callback_data.s, callback_data.b)
    if callback_data.b < view.total_batches:
        await svc.repo.set_current_batch(callback_data.s, callback_data.b + 1)
        await _render(callback, svc, SettingService(session),
                      callback_data.s, callback_data.b + 1)
    else:
        await callback.message.edit_text(
            "Все пачки проверены. Нажмите «✅ Завершить проверку» под задачей.")
    await session.commit()
    await callback.answer()


# ---------------------------------------------------------------------------
# Task 18: FSM фиксации ArticleAction (категория → проблема → решение →
# комментарий → дата следующей проверки), справочники читаются из БД.
# ---------------------------------------------------------------------------

async def active_categories(session) -> list[ArticleCategory]:
    return list(await session.scalars(
        select(ArticleCategory).where(ArticleCategory.is_active.is_(True))
        .order_by(ArticleCategory.sort_order)))


async def active_problem_types(session) -> list[ProblemType]:
    return list(await session.scalars(
        select(ProblemType).where(ProblemType.is_active.is_(True))
        .order_by(ProblemType.sort_order)))


async def active_decisions(session, problem_type_id: int | None) -> list[DecisionType]:
    all_active = list(await session.scalars(
        select(DecisionType).where(DecisionType.is_active.is_(True))
        .order_by(DecisionType.sort_order)))
    if problem_type_id is None:
        return all_active
    linked_ids = set(await session.scalars(
        select(ProblemDecisionLink.decision_type_id)
        .where(ProblemDecisionLink.problem_type_id == problem_type_id)))
    return (sorted(all_active, key=lambda d: (d.id not in linked_ids, d.sort_order))
            if linked_ids else all_active)


async def default_next_check_date(session, problem_type, decision_type) -> date:
    days = None
    if decision_type is not None and decision_type.default_next_check_days:
        days = decision_type.default_next_check_days
    elif problem_type is not None and problem_type.default_next_check_days:
        days = problem_type.default_next_check_days
    if days is None:
        days = int(await SettingService(session).get(
            "article_check.default_next_check_days"))
    return date.today() + timedelta(days=days)


async def _next_check_bounds(session) -> tuple[date, date]:
    settings = SettingService(session)
    min_days = int(await settings.get("article_check.next_check_min_days"))
    max_days = int(await settings.get("article_check.next_check_max_days"))
    return date.today() + timedelta(days=min_days), date.today() + timedelta(days=max_days)


async def _next_check_prompt(session, prob: ProblemType | None,
                             dec: DecisionType | None) -> str:
    default = await default_next_check_date(session, prob, dec)
    lo, hi = await _next_check_bounds(session)
    return (f"Шаг 5/5. Дата следующей проверки. По умолчанию: {default:%d.%m.%Y}.\n"
            f"Введите дату в формате ДД.ММ.ГГГГ (от {lo:%d.%m.%Y} до {hi:%d.%m.%Y}):")


async def _owned_item(session, actor, item_id: int | None):
    """Возвращает (item, chk_session), только если actor реально ответственный
    за сессию проверки этого артикула. Иначе (None, None) — единая точка проверки
    владения для всех шагов FSM (см. вывод security-ревью Task 17)."""
    if item_id is None:
        return None, None
    item = await ArticleCheckRepository(session).get_item(item_id)
    if item is None:
        return None, None
    chk_session = await session.get(ArticleCheckSession, item.check_session_id)
    if chk_session is None or chk_session.responsible_user_id != actor.id:
        return None, None
    return item, chk_session


async def start_action_fsm(callback: CallbackQuery, item_id: int, session,
                           state: FSMContext | None = None) -> None:
    cats = await active_categories(session)
    await callback.message.edit_text(
        "Шаг 1/5. Выберите категорию товара:",
        reply_markup=dict_keyboard(cats, "cat", item_id))
    if state is not None:
        await state.set_state(ArticleActionStates.category)
        await state.update_data(item_id=item_id)


async def _fsm_denied(callback: CallbackQuery) -> None:
    """Отказ БЕЗ мутации FSM-состояния и БД — actor не прошёл проверку владения."""
    await callback.answer("Недостаточно прав", show_alert=True)


async def _fsm_denied_message(message: Message) -> None:
    await message.answer("Недостаточно прав")


@router.callback_query(ActCb.filter(F.a == "cat"), StateFilter(ArticleActionStates.category))
async def handle_action_category(callback: CallbackQuery, callback_data: ActCb,
                                 session, state: FSMContext) -> None:
    actor = await UserService(session).get_actor(callback.from_user.id)
    if actor is None:
        await _fsm_denied(callback)
        return
    data = await state.get_data()
    item_id = data.get("item_id")
    if item_id is None or item_id != callback_data.it:
        await _fsm_denied(callback)             # чужой/устаревший/подделанный item_id
        return
    item, _chk_session = await _owned_item(session, actor, item_id)
    if item is None:
        await _fsm_denied(callback)
        return
    cat = await session.get(ArticleCategory, callback_data.id)
    if cat is None or not cat.is_active:
        await callback.answer("Категория недоступна", show_alert=True)
        return
    await state.update_data(category_id=cat.id)
    probs = await active_problem_types(session)
    await callback.message.edit_text(
        "Шаг 2/5. Выберите тип проблемы:",
        reply_markup=dict_keyboard(probs, "prob", item_id))
    await state.set_state(ArticleActionStates.problem)
    await callback.answer()


@router.callback_query(ActCb.filter(F.a == "prob"), StateFilter(ArticleActionStates.problem))
async def handle_action_problem(callback: CallbackQuery, callback_data: ActCb,
                                session, state: FSMContext) -> None:
    actor = await UserService(session).get_actor(callback.from_user.id)
    if actor is None:
        await _fsm_denied(callback)
        return
    data = await state.get_data()
    item_id = data.get("item_id")
    if item_id is None or item_id != callback_data.it:
        await _fsm_denied(callback)
        return
    item, _chk_session = await _owned_item(session, actor, item_id)
    if item is None:
        await _fsm_denied(callback)
        return
    prob = await session.get(ProblemType, callback_data.id)
    if prob is None or not prob.is_active:
        await callback.answer("Проблема недоступна", show_alert=True)
        return
    await state.update_data(problem_type_id=prob.id)
    decisions = await active_decisions(session, prob.id)
    await callback.message.edit_text(
        "Шаг 3/5. Выберите решение:",
        reply_markup=dict_keyboard(decisions, "dec", item_id))
    await state.set_state(ArticleActionStates.decision)
    await callback.answer()


@router.callback_query(ActCb.filter(F.a == "dec"), StateFilter(ArticleActionStates.decision))
async def handle_action_decision(callback: CallbackQuery, callback_data: ActCb,
                                 session, state: FSMContext) -> None:
    actor = await UserService(session).get_actor(callback.from_user.id)
    if actor is None:
        await _fsm_denied(callback)
        return
    data = await state.get_data()
    item_id = data.get("item_id")
    if item_id is None or item_id != callback_data.it:
        await _fsm_denied(callback)
        return
    item, _chk_session = await _owned_item(session, actor, item_id)
    if item is None:
        await _fsm_denied(callback)
        return
    dec = await session.get(DecisionType, callback_data.id)
    if dec is None or not dec.is_active:
        await callback.answer("Решение недоступно", show_alert=True)
        return
    await state.update_data(decision_type_id=dec.id)
    prob = await session.get(ProblemType, data.get("problem_type_id"))
    require_comment = bool(prob and prob.require_comment) or bool(dec.require_comment)
    if require_comment:
        text = "Шаг 4/5. Комментарий обязателен. Напишите комментарий текстом:"
        markup = None
    else:
        text = "Шаг 4/5. Комментарий (необязательно). Напишите текст или отправьте /skip:"
        markup = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
            text="⏭ Пропустить",
            callback_data=ActCb(a="skip", id=0, it=item_id).pack())]])
    await callback.message.edit_text(text, reply_markup=markup)
    await state.set_state(ArticleActionStates.comment)
    await callback.answer()


@router.callback_query(ActCb.filter(F.a == "skip"), StateFilter(ArticleActionStates.comment))
async def handle_action_skip_comment(callback: CallbackQuery, callback_data: ActCb,
                                     session, state: FSMContext) -> None:
    actor = await UserService(session).get_actor(callback.from_user.id)
    if actor is None:
        await _fsm_denied(callback)
        return
    data = await state.get_data()
    item_id = data.get("item_id")
    if item_id is None or item_id != callback_data.it:
        await _fsm_denied(callback)
        return
    item, _chk_session = await _owned_item(session, actor, item_id)
    if item is None:
        await _fsm_denied(callback)
        return
    prob = await session.get(ProblemType, data.get("problem_type_id"))
    dec = await session.get(DecisionType, data.get("decision_type_id"))
    if bool(prob and prob.require_comment) or bool(dec and dec.require_comment):
        await callback.answer("Комментарий обязателен, пропустить нельзя", show_alert=True)
        return
    await state.update_data(comment=None)
    await callback.message.edit_text(await _next_check_prompt(session, prob, dec))
    await state.set_state(ArticleActionStates.next_check)
    await callback.answer()


@router.message(StateFilter(ArticleActionStates.comment))
async def handle_action_comment(message: Message, session, state: FSMContext) -> None:
    actor = await UserService(session).get_actor(message.from_user.id)
    if actor is None:
        await _fsm_denied_message(message)
        return
    data = await state.get_data()
    item, _chk_session = await _owned_item(session, actor, data.get("item_id"))
    if item is None:
        await _fsm_denied_message(message)
        return
    prob = await session.get(ProblemType, data.get("problem_type_id"))
    dec = await session.get(DecisionType, data.get("decision_type_id"))
    require_comment = bool(prob and prob.require_comment) or bool(dec and dec.require_comment)
    text = (message.text or "").strip()
    if text == "/skip":
        if require_comment:
            await message.answer(
                "Комментарий обязателен, пропустить нельзя. Напишите комментарий:")
            return
        comment = None
    else:
        if not text:
            await message.answer("Комментарий не может быть пустым. Напишите текст или /skip:")
            return
        max_len = int(await SettingService(session).get("general.max_comment_length"))
        if len(text) > max_len:
            await message.answer(
                f"Слишком длинный комментарий (максимум {max_len} символов). "
                f"Сократите и отправьте снова:")
            return
        comment = text
    await state.update_data(comment=comment)
    await message.answer(await _next_check_prompt(session, prob, dec))
    await state.set_state(ArticleActionStates.next_check)


@router.message(StateFilter(ArticleActionStates.next_check))
async def handle_action_next_check(message: Message, session, state: FSMContext) -> None:
    actor = await UserService(session).get_actor(message.from_user.id)
    if actor is None:
        await _fsm_denied_message(message)
        return
    data = await state.get_data()
    item, chk_session = await _owned_item(session, actor, data.get("item_id"))
    if item is None:
        await _fsm_denied_message(message)
        return
    text = (message.text or "").strip()
    try:
        parsed = datetime.strptime(text, "%d.%m.%Y").date()
    except ValueError:
        await message.answer("Неверный формат даты. Введите в формате ДД.ММ.ГГГГ:")
        return
    lo, hi = await _next_check_bounds(session)
    if not (lo <= parsed <= hi):
        await message.answer(
            f"Дата должна быть от {lo:%d.%m.%Y} до {hi:%d.%m.%Y}. Введите снова:")
        return
    svc = ArticleCheckService(session, message.bot)
    try:
        await svc.create_action(
            item.id, actor, data.get("category_id"), data.get("problem_type_id"),
            data.get("decision_type_id"), data.get("comment"), parsed)
    except ValueError as exc:
        await message.answer(str(exc))
        return
    await state.clear()
    settings = SettingService(session)
    view = await svc.get_batch_view(chk_session.id, chk_session.current_batch)
    show_names = bool(await settings.get("article_check.show_product_name"))
    allow_prev = bool(await settings.get("article_check.allow_prev_batch"))
    await message.answer(render_batch(view, show_names),
                         reply_markup=batch_keyboard(view, allow_prev))
    await session.commit()
