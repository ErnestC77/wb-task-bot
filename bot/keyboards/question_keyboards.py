from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


class QstCb(CallbackData, prefix="q"):
    a: str    # ans
    id: int   # question_id


def answer_keyboard(question_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="💬 Ответить",
                             callback_data=QstCb(a="ans", id=question_id).pack())]])
