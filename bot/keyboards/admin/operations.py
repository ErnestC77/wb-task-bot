"""Клавиатуры разделов «▶ Ручной запуск» / «📋 Активные задачи» (Task 34).

`OpsCb` — CallbackData с префиксом "o" (не пересекается ни с одним другим:
ad/ar/cf/d/q/r/sc/sy/tc/tp/u). `a: str` без default, `id`/`p` — целочисленные
default-поля, безопасны как есть (Task 26 lesson). Строковых полей со
значением по умолчанию нет — баг Tasks 24-25 неприменим.

Кнопка «🔁 Переназначить» на карточке задачи использует ЧУЖОЙ `UsrCb` (Task 25,
bot/keyboards/admin/users.py) напрямую — переиспользование уже одобренного
флоу переназначения, а не дублирование (см. docstring bot/handlers/admin/
operations.py).
"""
from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.keyboards.admin.main import AdminCb
from bot.keyboards.admin.users import UsrCb


class OpsCb(CallbackData, prefix="o"):
    a: str
    id: int = 0
    p: int = 1


def run_list_keyboard(entries: list[tuple[int, str]], page: int,
                      total_pages: int) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=label, callback_data=OpsCb(a="runpick", id=cfg_id).pack())]
            for cfg_id, label in entries]
    if total_pages > 1:
        rows.append([
            InlineKeyboardButton(text="⬅", callback_data=OpsCb(a="runlist", p=max(1, page - 1)).pack()),
            InlineKeyboardButton(text=f"{page}/{total_pages}", callback_data=OpsCb(a="noop").pack()),
            InlineKeyboardButton(text="➡", callback_data=OpsCb(
                a="runlist", p=min(total_pages, page + 1)).pack()),
        ])
    rows.append([InlineKeyboardButton(
        text="⬅ Назад", callback_data=AdminCb(s="run", a="menu").pack())])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def active_list_keyboard(entries: list[tuple[int, str]], page: int,
                         total_pages: int) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=label, callback_data=OpsCb(a="card", id=inst_id).pack())]
            for inst_id, label in entries]
    if total_pages > 1:
        rows.append([
            InlineKeyboardButton(text="⬅", callback_data=OpsCb(a="actlist", p=max(1, page - 1)).pack()),
            InlineKeyboardButton(text=f"{page}/{total_pages}", callback_data=OpsCb(a="noop").pack()),
            InlineKeyboardButton(text="➡", callback_data=OpsCb(
                a="actlist", p=min(total_pages, page + 1)).pack()),
        ])
    rows.append([InlineKeyboardButton(
        text="⬅ Назад", callback_data=AdminCb(s="act", a="menu").pack())])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def instance_card_keyboard(instance_id: int, responsible_user_id: int | None,
                           waiting_approval: bool) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(
            text="📤 Повторить отправку", callback_data=OpsCb(a="resend", id=instance_id).pack())],
        [InlineKeyboardButton(
            text="✅ Завершить принудительно",
            callback_data=OpsCb(a="close_done", id=instance_id).pack())],
        [InlineKeyboardButton(
            text="❌ Отменить принудительно",
            callback_data=OpsCb(a="close_cancel", id=instance_id).pack())],
    ]
    if responsible_user_id is not None:
        rows.append([InlineKeyboardButton(
            text="🔁 Переназначить",
            callback_data=UsrCb(a="reassign_pick", id=instance_id,
                                k=str(responsible_user_id)).pack())])
    if waiting_approval:
        rows.append([InlineKeyboardButton(
            text="📨 Повторить запрос подтверждения",
            callback_data=OpsCb(a="resendappr", id=instance_id).pack())])
        rows.append([InlineKeyboardButton(
            text="⚡ Подтвердить автоматически сейчас",
            callback_data=OpsCb(a="autoapprove", id=instance_id).pack())])
    rows.append([InlineKeyboardButton(
        text="⬅ Назад", callback_data=OpsCb(a="actlist", p=1).pack())])
    return InlineKeyboardMarkup(inline_keyboard=rows)
