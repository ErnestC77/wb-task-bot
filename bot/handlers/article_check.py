from aiogram import F, Router
from aiogram.types import CallbackQuery

from bot.database.models import CheckStatus
from bot.keyboards.article_check_keyboards import (
    ChkCb, batch_keyboard, item_status_keyboard, render_batch,
)
from bot.keyboards.task_keyboards import TaskCb
from bot.services.article_check_service import ArticleCheckService
from bot.services.setting_service import SettingService
from bot.services.user_service import UserService

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
async def handle_mark(callback: CallbackQuery, callback_data: ChkCb, session):
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
        # запуск FSM вопроса по артикулу — Task 19
        from bot.handlers.questions import start_article_question
        await start_article_question(callback, callback_data.it, session)
        return
    if callback_data.st == "act":
        # запуск FSM фиксации действия — Task 18
        from bot.handlers.article_check import start_action_fsm
        await start_action_fsm(callback, callback_data.it, session)
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
