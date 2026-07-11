from aiogram.fsm.state import State, StatesGroup


class AdminStates(StatesGroup):
    """FSM-инфраструктура для админ-панели.

    waiting_value/waiting_confirm — универсальные состояния каркаса (Task 23).
    Специализированные состояния разделов добавляются в Tasks 24-27.
    """

    waiting_value = State()
    waiting_confirm = State()
