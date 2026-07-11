"""Вопросы по задаче/артикулу: личные сообщения получателю, ответ, эскалация.

Security note (Task 19 mandate, по итогам находок Task 17-18): каждый handler
здесь ДО любой мутации FSM/БД получает actor через UserService.get_actor и
проверяет его прямо на КОНКРЕТНОЙ сущности (ответственный за задачу/сессию
проверки — для вопроса; адресат вопроса (to_user_id) — для ответа). Просто
"зарегистрирован" недостаточно: чужой зарегистрированный пользователь не
должен суметь ни задать вопрос от чужого лица, ни ответить на чужой вопрос,
даже зная его id.
"""
from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.database.models import ArticleCheckSession, TaskInstance
from bot.database.repositories.article_check_repository import ArticleCheckRepository
from bot.database.repositories.question_repository import QuestionRepository
from bot.database.repositories.task_repository import TaskRepository
from bot.keyboards.article_check_keyboards import ChkCb
from bot.keyboards.question_keyboards import QstCb
from bot.keyboards.task_keyboards import TaskCb
from bot.services.question_service import QuestionService
from bot.services.setting_service import SettingService
from bot.services.user_service import UserService
from bot.states.question_states import QuestionStates

router = Router(name=__name__)


async def _fsm_denied(callback: CallbackQuery) -> None:
    """Отказ БЕЗ мутации FSM-состояния и БД — единая формулировка не даёт понять
    незарегистрированному/чужому пользователю, существует ли сущность вообще."""
    await callback.answer("Недостаточно прав", show_alert=True)


async def _fsm_denied_message(message: Message) -> None:
    await message.answer("Недостаточно прав")


async def _start_waiting_text(state: FSMContext | None, task_instance_id: int,
                              item_id: int | None) -> None:
    if state is not None:
        await state.set_state(QuestionStates.waiting_text)
        await state.update_data(task_instance_id=task_instance_id,
                                article_check_item_id=item_id)


@router.callback_query(TaskCb.filter(F.a == "question"))
async def handle_task_question(callback: CallbackQuery, callback_data: TaskCb, session,
                               state: FSMContext | None = None) -> None:
    """Кнопка «❓ Есть вопрос» под сообщением задачи (общий вопрос по задаче)."""
    actor = await UserService(session).get_actor(callback.from_user.id)
    if actor is None:
        await _fsm_denied(callback)
        return
    inst = await TaskRepository(session).get_instance(callback_data.i)
    if inst is None or inst.responsible_user_id != actor.id:
        await _fsm_denied(callback)              # чужая/несуществующая задача
        return
    await _start_waiting_text(state, inst.id, None)
    await callback.message.answer("Введите текст вопроса:")
    await callback.answer()


@router.callback_query(ChkCb.filter(F.a == "gq"))
async def handle_general_question(callback: CallbackQuery, callback_data: ChkCb, session,
                                  state: FSMContext | None = None) -> None:
    """Кнопка «❓ Общий вопрос» в пачке проверки артикулов (без привязки к артикулу)."""
    actor = await UserService(session).get_actor(callback.from_user.id)
    if actor is None:
        await _fsm_denied(callback)
        return
    chk_session = await session.get(ArticleCheckSession, callback_data.s)
    if chk_session is None or chk_session.responsible_user_id != actor.id:
        await _fsm_denied(callback)               # чужая/несуществующая сессия проверки
        return
    await _start_waiting_text(state, chk_session.task_instance_id, None)
    await callback.message.answer("Введите текст общего вопроса по проверке:")
    await callback.answer()


async def start_article_question(callback: CallbackQuery, item_id: int, session,
                                  state: FSMContext | None = None) -> None:
    """Вход из handle_mark (bot/handlers/article_check.py) при отметке «есть вопрос».

    handle_mark уже проверил через ArticleCheckService.mark(), что actor —
    ответственный за сессию проверки (иначе PermissionError), но здесь эта
    проверка повторяется самостоятельно (defense in depth) — функция публична
    и не должна полагаться только на то, что её единственный вызывающий уже
    всё проверил.
    """
    actor = await UserService(session).get_actor(callback.from_user.id)
    if actor is None:
        await _fsm_denied(callback)
        return
    item = await ArticleCheckRepository(session).get_item(item_id)
    if item is None:
        await _fsm_denied(callback)
        return
    chk_session = await session.get(ArticleCheckSession, item.check_session_id)
    if chk_session is None or chk_session.responsible_user_id != actor.id:
        await _fsm_denied(callback)
        return
    await _start_waiting_text(state, chk_session.task_instance_id, item.id)
    await callback.message.answer(
        f"Введите текст вопроса по артикулу {item.article_snapshot}:")
    await callback.answer()


@router.message(StateFilter(QuestionStates.waiting_text))
async def handle_question_text(message: Message, session, state: FSMContext) -> None:
    actor = await UserService(session).get_actor(message.from_user.id)
    if actor is None:
        await _fsm_denied_message(message)
        return
    data = await state.get_data()
    task_instance_id = data.get("task_instance_id")
    item_id = data.get("article_check_item_id")
    inst = (await session.get(TaskInstance, task_instance_id)
           if task_instance_id is not None else None)
    if inst is None:
        await _fsm_denied_message(message)
        await state.clear()
        return
    item = None
    if item_id is not None:
        item = await ArticleCheckRepository(session).get_item(item_id)
        chk_session = (await session.get(ArticleCheckSession, item.check_session_id)
                      if item is not None else None)
        if item is None or chk_session is None or chk_session.responsible_user_id != actor.id:
            await _fsm_denied_message(message)    # FSM-данные устарели/подделаны
            await state.clear()
            return
    elif inst.responsible_user_id != actor.id:
        await _fsm_denied_message(message)        # задача больше не принадлежит actor'у
        await state.clear()
        return
    text = (message.text or "").strip()
    if not text:
        await message.answer("Вопрос не может быть пустым. Напишите текст:")
        return
    max_len = int(await SettingService(session).get("general.max_question_length"))
    if len(text) > max_len:
        await message.answer(
            f"Слишком длинный вопрос (максимум {max_len} символов). "
            f"Сократите и отправьте снова:")
        return
    qsvc = QuestionService(session, message.bot)
    await qsvc.ask(inst, actor, text, item=item)
    await state.clear()
    await message.answer("Вопрос отправлен получателю.")
    await session.commit()


@router.callback_query(QstCb.filter(F.a == "ans"))
async def handle_answer_button(callback: CallbackQuery, callback_data: QstCb, session,
                               state: FSMContext | None = None) -> None:
    actor = await UserService(session).get_actor(callback.from_user.id)
    if actor is None:
        await _fsm_denied(callback)
        return
    question = await QuestionRepository(session).get(callback_data.id)
    # Ключевая проверка владения (Task 19 mandate): отвечать может только
    # адресат вопроса (to_user_id), а не любой зарегистрированный пользователь,
    # даже если он знает question_id — вопросы уходят в личку конкретному
    # получателю, и чужой пользователь не должен получить доступ к FSM ответа.
    if question is None or question.to_user_id != actor.id:
        await _fsm_denied(callback)
        return
    if state is not None:
        await state.set_state(QuestionStates.waiting_answer)
        await state.update_data(question_id=question.id)
    await callback.message.answer("Введите текст ответа:")
    await callback.answer()


@router.message(StateFilter(QuestionStates.waiting_answer))
async def handle_answer_text(message: Message, session, state: FSMContext) -> None:
    actor = await UserService(session).get_actor(message.from_user.id)
    if actor is None:
        await _fsm_denied_message(message)
        return
    data = await state.get_data()
    question_id = data.get("question_id")
    text = (message.text or "").strip()
    if not text:
        await message.answer("Ответ не может быть пустым. Напишите текст:")
        return
    qsvc = QuestionService(session, message.bot)
    try:
        await qsvc.answer(question_id, actor, text)
    except PermissionError:
        # Повторная проверка владения внутри QuestionService.answer() —
        # defense in depth на случай, если FSM-данные протухли/подделаны.
        await _fsm_denied_message(message)
        await state.clear()
        return
    except ValueError:
        await message.answer("Вопрос не найден.")
        await state.clear()
        return
    await state.clear()
    await message.answer("Ответ отправлен.")
    await session.commit()
