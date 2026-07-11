"""Клавиатуры разделов «🧾 Журнал изменений» / «💾 Резервные операции»
(Task 35).

`AudCb` (prefix="au") и `BakCb` (prefix="bk") — раздельные CallbackData-классы
для двух разделов с РАЗНЫМИ правами, живущих в одном модуле
bot/handlers/admin/audit.py (см. его docstring). Ни у одного нет строкового
поля с default — баг Tasks 24-25 (aiogram подменяет пустую строку на None
при unpack для nullable-полей) неприменим ни к одному из них. `mine: bool`
у `AudCb` — булево default-поле, аналогично безопасным int-полям с default
в остальных разделах: aiogram сериализует bool через `str(int(value))` —
"1"/"0", НЕ Python `str(bool(...))` ("True"/"False") — пустой строки не
возникает ни при каком значении, риска нет (проверено эмпирически на
установленной версии aiogram, см. `test_audcb_default_fields_roundtrip`,
покрывающий именно default-значение `mine=False`).
"""
from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.keyboards.admin.main import AdminCb


class AudCb(CallbackData, prefix="au"):
    a: str
    p: int = 1
    mine: bool = False


class BakCb(CallbackData, prefix="bk"):
    a: str


def audit_page_keyboard(page: int, mine_only: bool) -> InlineKeyboardMarkup:
    toggle_text = "👤 Только мои действия" if not mine_only else "👥 Все действия"
    rows = [
        [
            InlineKeyboardButton(
                text="⬅", callback_data=AudCb(a="page", p=max(1, page - 1), mine=mine_only).pack()),
            InlineKeyboardButton(text=str(page), callback_data=AudCb(a="noop").pack()),
            InlineKeyboardButton(
                text="➡", callback_data=AudCb(a="page", p=page + 1, mine=mine_only).pack()),
        ],
        [InlineKeyboardButton(
            text=toggle_text, callback_data=AudCb(a="toggle", mine=mine_only).pack())],
        [InlineKeyboardButton(text="⬅ Назад", callback_data=AdminCb(s="aud", a="menu").pack())],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def backup_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="🔁 Восстановить job'ы планировщика",
            callback_data=BakCb(a="recoverjobs").pack())],
        [InlineKeyboardButton(
            text="📬 Восстановить зависшие доставки",
            callback_data=BakCb(a="recoverdeliv").pack())],
        [InlineKeyboardButton(text="⬅ Назад", callback_data=AdminCb(s="bak", a="menu").pack())],
    ])
