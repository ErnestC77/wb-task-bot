from aiogram.types import InlineKeyboardButton

from bot.keyboards.admin.main import AdminCb


def pagination_row(section: str, page: int, total_pages: int) -> list[InlineKeyboardButton]:
    return [
        InlineKeyboardButton(text="⬅", callback_data=AdminCb(
            s=section, a="page", p=max(1, page - 1)).pack()),
        InlineKeyboardButton(text=f"{page}/{total_pages}", callback_data=AdminCb(
            s=section, a="noop").pack()),
        InlineKeyboardButton(text="➡", callback_data=AdminCb(
            s=section, a="page", p=min(total_pages, page + 1)).pack()),
    ]
