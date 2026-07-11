from aiogram.fsm.state import State, StatesGroup


class QuestionStates(StatesGroup):
    waiting_text = State()      # спрашивающий вводит текст вопроса
    waiting_answer = State()    # получатель вводит текст ответа
