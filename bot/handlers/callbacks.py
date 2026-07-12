"""Callbacks основной задачи (task_keyboards) и подтверждений (approval_keyboards).

Security note (Task 20 mandate, по итогам находок Tasks 17-19): каждый handler
здесь ДО любой мутации БД получает actor через UserService.get_actor и либо
отказывает при actor is None, либо перепроверяет владение конкретной задачей
через сервисный слой (TaskService.ensure_responsible / ApprovalService.can_approve),
а не дублирует эту логику вручную. Повторное нажатие на уже обработанную задачу
(conditional UPDATE в TaskRepository.transition_status вернёт None) отвечает
«Уже обработано» — идемпотентность без второй записи в TaskLog.

Старая механика с отдельной кнопкой статуса «требуется внимание» (кнопки нет
в task_keyboards.py) сюда не входит — она убрана из плана целиком, включая
любые обработчики для неё.
"""
from datetime import datetime

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from bot.database.models import TaskStatus
from bot.keyboards.approval_keyboards import ApproveCb
from bot.keyboards.task_keyboards import TaskCb
from bot.services.approval_service import ApprovalService
from bot.services.article_check_service import ArticleCheckService
from bot.services.task_service import TaskService
from bot.services.user_service import UserService

router = Router(name=__name__)


class ReturnCommentStates(StatesGroup):
    waiting = State()


@router.callback_query(TaskCb.filter(F.a == "postpone"))
async def handle_postpone(callback: CallbackQuery, callback_data: TaskCb, session):
    actor = await UserService(session).get_actor(callback.from_user.id)
    if actor is None:
        await callback.answer("Недостаточно прав", show_alert=True)
        return
    svc = TaskService(session, callback.bot)
    try:
        got = await svc.postpone_to_tomorrow(callback_data.i, actor)
    except PermissionError:
        await callback.answer("Недостаточно прав: вы не ответственный", show_alert=True)
        return
    await session.commit()
    await callback.answer("Перенесено на завтра" if got else "Уже обработано")


@router.callback_query(TaskCb.filter(F.a == "done"))
async def handle_done(callback: CallbackQuery, callback_data: TaskCb, session):
    """simple-сценарий: completed (без approval) либо waiting_approval (с ним)."""
    actor = await UserService(session).get_actor(callback.from_user.id)
    if actor is None:
        await callback.answer("Недостаточно прав", show_alert=True)
        return
    tasks = TaskService(session, callback.bot)
    inst = await tasks.repo.get_instance(callback_data.i)
    if inst is None:
        await callback.answer("Недоступно", show_alert=True)
        return
    try:
        if inst.need_approval_snapshot:
            got = await ApprovalService(session, callback.bot).request_approval(inst, actor)
            done_msg = "Отправлено на подтверждение ⏳"
        else:
            got = await tasks.user_transition(
                inst.id, [TaskStatus.IN_PROGRESS], TaskStatus.COMPLETED, actor,
                "btn:done", completed_at=datetime.utcnow())
            done_msg = "Выполнено ✅"
    except PermissionError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await session.commit()
    await callback.answer(done_msg if got else "Уже обработано")


@router.callback_query(TaskCb.filter(F.a == "finish"))
async def handle_finish(callback: CallbackQuery, callback_data: TaskCb, session):
    actor = await UserService(session).get_actor(callback.from_user.id)
    if actor is None:
        await callback.answer("Недостаточно прав", show_alert=True)
        return
    svc = ArticleCheckService(session, callback.bot)
    inst = await svc.tasks.get_instance(callback_data.i)
    if inst is None:
        await callback.answer("Недоступно", show_alert=True)
        return
    try:
        ok, reason = await svc.finish_check(inst, actor)
    except PermissionError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    if not ok:
        await callback.answer(reason, show_alert=True)
        return
    # finish_check() уже перевёл задачу в waiting_approval (с уведомлением
    # подтверждающих через ApprovalService.request_approval) либо в completed
    # (с обновлением сообщения задачи) — второй вызов notify/refresh здесь
    # был бы дублирующим уведомлением, поэтому не повторяем его.
    await session.commit()
    await callback.answer("Проверка завершена ✅")


@router.callback_query(ApproveCb.filter(F.a == "ok"))
async def handle_approve(callback: CallbackQuery, callback_data: ApproveCb, session):
    actor = await UserService(session).get_actor(callback.from_user.id)
    svc = ApprovalService(session, callback.bot)
    if actor is None or not await svc.can_approve(actor):
        await callback.answer("Подтверждать могут только owner/partner", show_alert=True)
        return
    got = await svc.approve(callback_data.i, actor)
    await session.commit()
    if got is not None:
        # Убираем кнопки с сообщения-запроса на подтверждение — иначе оно
        # остаётся кликабельным (повторное нажатие лишь отвечало «Уже
        # обработано», но визуально ничего не менялось).
        try:
            await callback.message.edit_text(
                callback.message.html_text + "\n\n✅ Подтверждено", reply_markup=None)
        except Exception:                              # noqa: BLE001
            pass
    await callback.answer("Подтверждено ✅" if got else "Уже обработано")


@router.callback_query(ApproveCb.filter(F.a == "back"))
async def handle_return_request(callback: CallbackQuery, callback_data: ApproveCb,
                                session, state: FSMContext):
    actor = await UserService(session).get_actor(callback.from_user.id)
    svc = ApprovalService(session, callback.bot)
    if actor is None or not await svc.can_approve(actor):
        await callback.answer("Недостаточно прав", show_alert=True)
        return
    await state.set_state(ReturnCommentStates.waiting)
    await state.update_data(
        instance_id=callback_data.i,
        approval_chat_id=callback.message.chat.id,
        approval_message_id=callback.message.message_id,
        approval_html_text=callback.message.html_text)
    await callback.message.answer("Введите комментарий для возврата в работу:")
    await callback.answer()


@router.message(ReturnCommentStates.waiting)
async def handle_return_comment(message: Message, session, state: FSMContext):
    actor = await UserService(session).get_actor(message.from_user.id)
    if actor is None:
        # Буквальный код брифа звал svc.return_to_work(..., None, ...) без этой
        # проверки -> ApprovalService.can_approve(None) упал бы AttributeError на None.role.
        await message.answer("Недостаточно прав")
        await state.clear()
        return
    data = await state.get_data()
    instance_id = data.get("instance_id")
    if instance_id is None:
        await message.answer("Недостаточно прав")   # FSM-данные устарели/подделаны
        await state.clear()
        return
    svc = ApprovalService(session, message.bot)
    try:
        got = await svc.return_to_work(instance_id, actor, message.text or "")
    except (PermissionError, ValueError) as exc:
        # PermissionError — роль actor'а изменилась между callback'ом и сообщением
        # (can_approve() внутри return_to_work перепроверяет заново, defense-in-depth).
        await message.answer(str(exc))
        await state.clear()
        return
    await state.clear()
    await session.commit()
    if got is not None:
        approval_chat_id = data.get("approval_chat_id")
        approval_message_id = data.get("approval_message_id")
        if approval_chat_id is not None and approval_message_id is not None:
            try:
                await message.bot.edit_message_text(
                    chat_id=approval_chat_id, message_id=approval_message_id,
                    text=(data.get("approval_html_text") or "") + "\n\n🔁 Возвращено в работу",
                    reply_markup=None)
            except Exception:                          # noqa: BLE001
                pass
    await message.answer("🔁 Возвращено в работу" if got else "Уже обработано")
