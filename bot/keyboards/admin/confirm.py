from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


class ConfirmCb(CallbackData, prefix="cf"):
    t: str
    ok: bool


def confirm_keyboard(token: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Подтвердить",
                             callback_data=ConfirmCb(t=token, ok=True).pack()),
        InlineKeyboardButton(text="❌ Отмена",
                             callback_data=ConfirmCb(t=token, ok=False).pack()),
    ]])
