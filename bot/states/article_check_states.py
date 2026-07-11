from aiogram.fsm.state import State, StatesGroup


class ArticleActionStates(StatesGroup):
    category = State()
    problem = State()
    decision = State()
    comment = State()
    next_check = State()
