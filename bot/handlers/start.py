"""/start, /help, /cancel.

/start работает и для незарегистрированного пользователя (это ожидаемо —
actor может ещё не существовать: пользователей регистрирует
администратор/синхронизация с Google Sheets, а не сам /start). В этом случае
никакой мутации БД не происходит — только информационное сообщение.

/cancel — универсальный сброс FSM: `state.clear()` работает одинаково для
ЛЮБОГО активного состояния (ArticleActionStates из Task 18, QuestionStates
из Task 19, ReturnCommentStates из Task 20) — aiogram FSMContext не завязан
на конкретную StatesGroup, поэтому один и тот же вызов закрывает любой сценарий.
"""
from aiogram import Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from bot.database.models import Role
from bot.services.user_service import UserService
from bot.utils.html_utils import html_escape

router = Router(name=__name__)

ROLE_LABELS = {
    Role.OWNER: "владелец",
    Role.PARTNER: "партнёр",
    Role.MANAGER_WB: "менеджер WB",
    Role.LOGISTIC: "логист",
}

HELP_TEXT = (
    "📋 Доступные команды:\n"
    "/today — задачи на сегодня\n"
    "/my_tasks — мои открытые задачи\n"
    "/overdue — просроченные задачи\n"
    "/cancel — отменить текущее действие\n"
    "/help — эта справка\n\n"
    "Действиями по задаче (начать/перенести/завершить/задать вопрос) "
    "управляют кнопки под сообщением задачи."
)


@router.message(Command("start"))
async def cmd_start(message: Message, session) -> None:
    actor = await UserService(session).get_actor(message.from_user.id)
    if actor is None:
        await message.answer(
            "Вы не зарегистрированы в системе. Обратитесь к администратору, "
            "чтобы вас добавили как сотрудника.")
        return
    if message.chat.type == "private":
        await UserService(session).mark_private_chat_available(actor.telegram_id)
        await session.commit()
    role_label = ROLE_LABELS.get(actor.role, actor.role)
    await message.answer(
        f"Здравствуйте, {html_escape(actor.name)}! Ваша роль: {role_label}.\n"
        f"Наберите /help, чтобы увидеть список доступных команд.")


@router.message(Command("help"))
async def cmd_help(message: Message, session) -> None:
    # Осознанное исключение из мандата «actor-check везде кроме /start»:
    # /help не мутирует БД и не раскрывает чужие данные, actor здесь нужен
    # только для персонализации текста (owner/partner приписка ниже).
    # Незарегистрированный пользователь должен иметь доступ к /help — иначе
    # он никогда не узнает, что делать/как зарегистрироваться.
    actor = await UserService(session).get_actor(message.from_user.id)
    text = HELP_TEXT
    if actor is not None and actor.role in (Role.OWNER, Role.PARTNER):
        text += ("\n\nКак владелец/партнёр вы видите задачи всех сотрудников "
                "(/today, /my_tasks, /overdue) и можете подтверждать выполненные "
                "задачи кнопками под уведомлением.")
    await message.answer(text)


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    # Осознанное исключение из мандата «actor-check везде кроме /start»:
    # aiogram FSMContext ключуется per-chat/per-user (bot_id, chat_id,
    # user_id), поэтому state.clear() очищает только FSM-состояние самого
    # вызывающего — чужим состоянием физически нельзя завладеть через эту
    # команду, проверять нечего.
    await state.clear()
    await message.answer("Действие отменено")
