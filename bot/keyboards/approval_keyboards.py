"""Клавиатура подтверждения задачи владельцем/партнёром."""
from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


class ApproveCb(CallbackData, prefix="ap"):
    a: str   # ok|back
    i: int


def approval_keyboard(instance_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Подтвердить",
                             callback_data=ApproveCb(a="ok", i=instance_id).pack()),
        InlineKeyboardButton(text="🔁 Вернуть в работу",
                             callback_data=ApproveCb(a="back", i=instance_id).pack()),
    ]])
