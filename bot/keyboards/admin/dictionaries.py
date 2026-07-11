"""Клавиатуры раздела админ-панели «⚠ Категории проблем» / «🛠 Варианты решений»
(+ «Категории товаров») (Task 30).

`DicCb` — отдельный `CallbackData`-класс с префиксом "d" для навигации внутри
раздела (список одного из трёх справочников -> карточка -> добавить/
переименовать/переставить/деактивировать-восстановить/удалить (только для
неиспользуемых записей) -> для проблем — «🔗 Рекомендуемые решения»).

Отклонение от буквального интерфейса брифа (`DicCb.dict: str` со значениями
"categories|problems|decisions"): поле переименовано в `kind` (не `dict` —
`dict` теряется как удобное имя внутри модуля, где он же используется как
builtin, плюс совпадает с deprecated pydantic `BaseModel.dict()`, что путает
при чтении `callback_data.dict`) и типизировано как `Literal[...]` РОВНО с
теми же тремя ключами, что `DICT_MODELS` в handlers (`article_categories`,
`problem_types`, `decision_types`) — без промежуточного маппинга «короткое
название -> ключ реестра». aiogram/pydantic валидируют `Literal` на `.unpack()`
так же, как и обещал бриф: недопустимое значение `kind` не пройдёт unpack.

Действие переупорядочивания (⬆/⬇) закодировано ДВУМЯ отдельными значениями
`a` ("up"/"down"), а не отдельным полем-направлением — по аналогии с
`editname`/`editorder`/`editresp` в `ArtCb` (Task 29): проще на одно поле
`CallbackData`, чем заводить `dir: str | None`.

Целочисленные поля со значением по умолчанию (`id: int = 0`, `id2: int = 0`,
`p: int = 1`) безопасны без изменений (см. Task 26: баг Tasks 24-25 — именно
`str = ""` default, не целочисленный). Строковых default-полей в `DicCb` нет
вообще. Каждая клавиатура покрыта тестом, реально вызывающим `.pack()`/
`.unpack()` (см. tests/test_admin_dictionaries.py).
"""
from typing import Literal

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.keyboards.admin.main import AdminCb

DictKind = Literal["article_categories", "problem_types", "decision_types"]

KIND_TITLES: dict[str, str] = {
    "article_categories": "📦 Категории товаров",
    "problem_types": "⚠ Категории проблем",
    "decision_types": "🛠 Варианты решений",
}


class DicCb(CallbackData, prefix="d"):
    a: str
    kind: DictKind
    id: int = 0
    id2: int = 0
    p: int = 1


def kind_switcher_row(current: str) -> list[InlineKeyboardButton]:
    return [InlineKeyboardButton(text=("• " if k == current else "") + KIND_TITLES[k],
                                 callback_data=DicCb(a="list", kind=k).pack())
            for k in ("article_categories", "problem_types", "decision_types")]


def dictionary_list_keyboard(kind: str, entries: list[tuple[int, str]], page: int,
                             total_pages: int) -> InlineKeyboardMarkup:
    """`entries` — [(entry_id, отображаемая строка с пометкой «(неактивна)» для
    неактивных), ...] уже одной страницы, отсортированные по sort_order."""
    rows = [[InlineKeyboardButton(text=label, callback_data=DicCb(a="card", kind=kind, id=eid).pack())]
            for eid, label in entries]
    rows.append([InlineKeyboardButton(
        text="➕ Добавить", callback_data=DicCb(a="add", kind=kind).pack())])
    if total_pages > 1:
        rows.append([
            InlineKeyboardButton(text="⬅", callback_data=DicCb(
                a="list", kind=kind, p=max(1, page - 1)).pack()),
            InlineKeyboardButton(text=f"{page}/{total_pages}",
                                 callback_data=DicCb(a="noop", kind=kind).pack()),
            InlineKeyboardButton(text="➡", callback_data=DicCb(
                a="list", kind=kind, p=min(total_pages, page + 1)).pack()),
        ])
    rows.append(kind_switcher_row(kind))
    rows.append([InlineKeyboardButton(
        text="⬅ Назад", callback_data=AdminCb(s="dic", a="menu").pack())])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def dictionary_card_keyboard(kind: str, entry_id: int, is_active: bool,
                             show_delete: bool, show_links: bool) -> InlineKeyboardMarkup:
    toggle_text = "🚫 Деактивировать" if is_active else "♻ Восстановить"
    rows = [
        [InlineKeyboardButton(text="✏ Переименовать",
                              callback_data=DicCb(a="rename", kind=kind, id=entry_id).pack())],
        [
            InlineKeyboardButton(text="⬆", callback_data=DicCb(a="up", kind=kind, id=entry_id).pack()),
            InlineKeyboardButton(text="⬇", callback_data=DicCb(a="down", kind=kind, id=entry_id).pack()),
        ],
        [InlineKeyboardButton(text=toggle_text,
                              callback_data=DicCb(a="toggle", kind=kind, id=entry_id).pack())],
    ]
    if show_links:
        rows.append([InlineKeyboardButton(
            text="🔗 Рекомендуемые решения",
            callback_data=DicCb(a="links", kind=kind, id=entry_id).pack())])
    if show_delete:
        rows.append([InlineKeyboardButton(
            text="🗑 Удалить", callback_data=DicCb(a="delete", kind=kind, id=entry_id).pack())])
    rows.append([InlineKeyboardButton(
        text="⬅ Назад", callback_data=DicCb(a="list", kind=kind, p=1).pack())])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def links_keyboard(problem_id: int, entries: list[tuple[int, str, bool]]) -> InlineKeyboardMarkup:
    """`entries` — [(decision_type_id, название, linked_bool), ...] — все активные
    решения с пометкой ✅/▫, клик — toggle."""
    rows = [[InlineKeyboardButton(
        text=f"{'✅' if linked else '▫'} {label}",
        callback_data=DicCb(a="link", kind="problem_types", id=problem_id, id2=did).pack())]
        for did, label, linked in entries]
    rows.append([InlineKeyboardButton(
        text="⬅ Назад", callback_data=DicCb(a="card", kind="problem_types", id=problem_id).pack())])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def yes_no_keyboard(kind: str, action: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Да", callback_data=DicCb(a=action, kind=kind, id=1).pack()),
        InlineKeyboardButton(text="Нет", callback_data=DicCb(a=action, kind=kind, id=0).pack()),
    ]])


def cancel_keyboard(kind: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
        text="❌ Отменить", callback_data=DicCb(a="list", kind=kind, p=1).pack())]])
