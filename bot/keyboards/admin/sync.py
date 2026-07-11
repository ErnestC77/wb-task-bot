"""Клавиатуры раздела админ-панели «🔄 Синхронизация Google Sheets» (Task 33).

`SynCb` — CallbackData с префиксом "sy" (не пересекается с "sc" — Расписания,
Task 28: aiogram сравнивает префикс как отдельный токен до первого ":",
"sy" и "sc" — разные токены, коллизии нет). `a: str` без default (обязательное
поле), `id`/`p` — целочисленные default-поля, безопасны как есть (Task 26
lesson). Строковых полей со значением по умолчанию здесь нет вовсе — баг
Tasks 24-25 (aiogram подменяет пустую строку на None при unpack) в принципе
неприменим к этому классу.
"""
from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.keyboards.admin.main import AdminCb


class SynCb(CallbackData, prefix="sy"):
    a: str
    id: int = 0
    p: int = 1


def sync_status_keyboard(auto_enabled: bool) -> InlineKeyboardMarkup:
    toggle_text = ("⏸ Выключить автосинхронизацию" if auto_enabled
                   else "▶ Включить автосинхронизацию")
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="▶ Dry-run", callback_data=SynCb(a="dryrun").pack())],
        [InlineKeyboardButton(text="✅ Применить", callback_data=SynCb(a="apply").pack())],
        [InlineKeyboardButton(text=toggle_text, callback_data=SynCb(a="toggle").pack())],
        [InlineKeyboardButton(text="⚙ Настройки", callback_data=SynCb(a="list", p=1).pack())],
        [InlineKeyboardButton(text="⬅ Назад", callback_data=AdminCb(s="syn", a="menu").pack())],
    ])


def sync_list_keyboard(entries: list[tuple[int, str]], page: int,
                       total_pages: int) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=label, callback_data=SynCb(a="card", id=idx).pack())]
            for idx, label in entries]
    if total_pages > 1:
        rows.append([
            InlineKeyboardButton(text="⬅", callback_data=SynCb(a="list", p=max(1, page - 1)).pack()),
            InlineKeyboardButton(text=f"{page}/{total_pages}", callback_data=SynCb(a="noop").pack()),
            InlineKeyboardButton(text="➡", callback_data=SynCb(
                a="list", p=min(total_pages, page + 1)).pack()),
        ])
    rows.append([InlineKeyboardButton(text="⬅ Назад", callback_data=SynCb(a="status").pack())])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def sync_card_keyboard(idx: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏ Изменить", callback_data=SynCb(a="edit", id=idx).pack())],
        [InlineKeyboardButton(text="⬅ Назад", callback_data=SynCb(a="list", p=1).pack())],
    ])


def status_back_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
        text="⬅ Назад", callback_data=SynCb(a="status").pack())]])


def cancel_keyboard(idx: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
        text="❌ Отменить", callback_data=SynCb(a="card", id=idx).pack())]])
