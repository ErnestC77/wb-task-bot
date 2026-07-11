"""Клавиатуры раздела админ-панели «📦 Артикулы» (Task 29).

`ArtCb` — отдельный `CallbackData`-класс с префиксом "ar" для навигации внутри
раздела (список артикулов, фильтр архивных -> карточка -> редактирование
поля/toggle активности -> «➕ Добавить вручную»), по образцу `SchCb` (Task 28) /
`CfgCb` (Task 27) / `UsrCb`/`TopCb` (Tasks 25-26).

Бриф Task 29 задаёт интерфейс `ArtCb`: `a: str`, `id: int = 0`, `p: int = 1`
(без поля для редактирования по индексу). Здесь редактируемых полей всего
три (название/sort_order/ответственный) — вместо whitelist-по-индексу (как в
Tasks 24/27/28, где полей 8-17) каждое из трёх имеет СВОЙ код действия
(`a="editname"`/`a="editorder"`/`a="editresp"`) — это тоже whitelist (закрытый
список значений `a`, обрабатываемых `if/elif` в handler'е), просто не через
числовой индекс, а через имя действия; ни один callback не передаёт имя поля
модели напрямую в `setattr`.

Все поля `ArtCb` — целочисленные с default (`id: int = 0`, `p: int = 1`) —
безопасны без изменений (системный баг Tasks 24-26 — `str = ""` default,
здесь строковых default-полей нет вообще, поэтому и упоминать нечего). Каждая
клавиатура ниже покрыта тестом, реально вызывающим `.pack()`/`.unpack()`
(см. tests/test_admin_articles.py).
"""
from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.keyboards.admin.main import AdminCb


class ArtCb(CallbackData, prefix="ar"):
    a: str
    id: int = 0
    p: int = 1


def articles_list_keyboard(entries: list[tuple[int, str]], page: int, total_pages: int,
                           allow_add: bool) -> InlineKeyboardMarkup:
    """`entries` — [(article_id, отображаемая строка), ...] уже одной страницы."""
    rows = [[InlineKeyboardButton(text=label, callback_data=ArtCb(a="card", id=article_id).pack())]
            for article_id, label in entries]
    if allow_add:
        rows.append([InlineKeyboardButton(
            text="➕ Добавить вручную", callback_data=ArtCb(a="add").pack())])
    if total_pages > 1:
        rows.append([
            InlineKeyboardButton(text="⬅", callback_data=ArtCb(
                a="list", p=max(1, page - 1)).pack()),
            InlineKeyboardButton(text=f"{page}/{total_pages}",
                                 callback_data=ArtCb(a="noop").pack()),
            InlineKeyboardButton(text="➡", callback_data=ArtCb(
                a="list", p=min(total_pages, page + 1)).pack()),
        ])
    rows.append([InlineKeyboardButton(
        text="⬅ Назад", callback_data=AdminCb(s="art", a="menu").pack())])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def article_card_keyboard(article_id: int, is_active: bool) -> InlineKeyboardMarkup:
    toggle_text = "🚫 Деактивировать" if is_active else "✅ Активировать"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏ Название",
                              callback_data=ArtCb(a="editname", id=article_id).pack())],
        [InlineKeyboardButton(text="🔢 Порядок сортировки",
                              callback_data=ArtCb(a="editorder", id=article_id).pack())],
        [InlineKeyboardButton(text="👤 Ответственный",
                              callback_data=ArtCb(a="editresp", id=article_id).pack())],
        [InlineKeyboardButton(text=toggle_text,
                              callback_data=ArtCb(a="toggle", id=article_id).pack())],
        [InlineKeyboardButton(text="⬅ Назад", callback_data=ArtCb(a="list", p=1).pack())],
    ])


def cancel_edit_keyboard(article_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
        text="❌ Отменить", callback_data=ArtCb(a="card", id=article_id).pack())]])


def cancel_add_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
        text="❌ Отменить", callback_data=ArtCb(a="list", p=1).pack())]])
