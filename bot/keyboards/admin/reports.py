"""Клавиатуры раздела админ-панели «📊 Отчёты» (Task 32).

`RepCb` — отдельный `CallbackData`-класс с префиксом "r" (единственная
категория настроек `reports` — в отличие от «Маршрутизации вопросов»/
«Настроек», карточка/категория здесь не нужна отдельным полем, только индекс
ключа в отсортированном `category_keys("reports")`). Как и во всех разделах
с Task 24, целочисленные default-поля (`id`/`p`) безопасны без изменений —
баг aiogram (пустая строка -> None при unpack) касается только строковых
полей со значением по умолчанию, а здесь таких нет.
"""
from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.keyboards.admin.main import AdminCb


class RepCb(CallbackData, prefix="r"):
    a: str
    id: int = 0
    p: int = 1


def reports_list_keyboard(entries: list[tuple[int, str]], page: int,
                          total_pages: int) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=label, callback_data=RepCb(a="card", id=idx).pack())]
            for idx, label in entries]
    if total_pages > 1:
        rows.append([
            InlineKeyboardButton(text="⬅", callback_data=RepCb(a="list", p=max(1, page - 1)).pack()),
            InlineKeyboardButton(text=f"{page}/{total_pages}", callback_data=RepCb(a="noop").pack()),
            InlineKeyboardButton(text="➡", callback_data=RepCb(
                a="list", p=min(total_pages, page + 1)).pack()),
        ])
    rows.append([InlineKeyboardButton(text="👁 Предпросмотр", callback_data=RepCb(a="preview").pack())])
    rows.append([InlineKeyboardButton(
        text="📤 Сформировать и отправить сейчас", callback_data=RepCb(a="send").pack())])
    rows.append([InlineKeyboardButton(
        text="⬅ Назад", callback_data=AdminCb(s="rep", a="menu").pack())])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def report_setting_card_keyboard(idx: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏ Изменить", callback_data=RepCb(a="edit", id=idx).pack())],
        [InlineKeyboardButton(text="⬅ Назад", callback_data=RepCb(a="list", p=1).pack())],
    ])


def back_to_list_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
        text="⬅ Назад", callback_data=RepCb(a="list", p=1).pack())]])


def cancel_keyboard(idx: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
        text="❌ Отменить", callback_data=RepCb(a="card", id=idx).pack())]])
