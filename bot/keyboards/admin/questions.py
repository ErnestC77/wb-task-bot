"""Клавиатуры раздела админ-панели «❓ Маршрутизация вопросов» (Task 31).

`QstCb` — отдельный `CallbackData`-класс с префиксом "q" для навигации внутри
раздела (список 8 настроек категории `questions` -> карточка -> для трёх
"получатель"-ключей — пикер активного пользователя с открытой личкой; для двух
"маршрут"-ключей (`route_by_topic`/`route_by_category`) — двухшаговый пикер
«тема/категория -> пользователь»; для остальных ключей — обычный текстовый
FSM-ввод, как в разделе «Настройки», Task 24).

Пикеры получателя/маршрута реализованы БЕЗ FSM — весь выбор кодируется цепочкой
callback_data (`routekey` несёт выбранный `map_key` дальше в `routeset`), а не
хранится в промежуточном FSM-состоянии: для двух кликов подряд это проще и
надёжнее (нет риска забыть сбросить состояние при отмене, как это уже дважды
находил ревью — Task 28/30).

Поля: `id: int = 0` — индекс ключа настройки в отсортированном списке
`category_keys("questions")` (контекст «список -> карточка -> редактирование»)
ИЛИ id пользователя (контекст `setrecv`/`routeset`, где `id2` — вторичное целое
поле). `k: str | None = None` — `map_key` (topic_key/имя категории) для
маршрутов; НЕ `str = ""` (системный баг Tasks 24-25 — aiogram трактует ЛЮБОЕ
поле со значением по умолчанию как nullable и подменяет пустую строку на
`None` при `unpack()`, что pydantic отклоняет для не-Optional `str`).
Целочисленные default-поля (`id`/`id2`/`p`) безопасны без изменений (Task 26).
Каждая клавиатура покрыта тестом, реально вызывающим `.pack()`/`.unpack()`
(см. tests/test_admin_questions.py).
"""
from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.keyboards.admin.main import AdminCb


class QstCb(CallbackData, prefix="q"):
    a: str
    id: int = 0
    id2: int = 0
    k: str | None = None
    p: int = 1


def questions_list_keyboard(entries: list[tuple[int, str]], page: int,
                            total_pages: int) -> InlineKeyboardMarkup:
    """`entries` — [(index_в_category_keys, "ключ = значение"), ...] одной страницы."""
    rows = [[InlineKeyboardButton(text=label, callback_data=QstCb(a="card", id=idx).pack())]
            for idx, label in entries]
    if total_pages > 1:
        rows.append([
            InlineKeyboardButton(text="⬅", callback_data=QstCb(a="list", p=max(1, page - 1)).pack()),
            InlineKeyboardButton(text=f"{page}/{total_pages}", callback_data=QstCb(a="noop").pack()),
            InlineKeyboardButton(text="➡", callback_data=QstCb(
                a="list", p=min(total_pages, page + 1)).pack()),
        ])
    rows.append([InlineKeyboardButton(
        text="⬅ Назад", callback_data=AdminCb(s="qst", a="menu").pack())])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def question_setting_card_keyboard(idx: int, editable: bool) -> InlineKeyboardMarkup:
    rows = []
    if editable:
        rows.append([InlineKeyboardButton(
            text="✏ Изменить", callback_data=QstCb(a="edit", id=idx).pack())])
    rows.append([InlineKeyboardButton(text="⬅ Назад", callback_data=QstCb(a="list", p=1).pack())])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def receiver_picker_keyboard(idx: int, users: list[tuple[int, str]]) -> InlineKeyboardMarkup:
    """`users` — [(user_id, отображаемое имя), ...] активных пользователей с
    открытой личкой (private_chat_available=True)."""
    rows = [[InlineKeyboardButton(text=name, callback_data=QstCb(a="setrecv", id=idx, id2=uid).pack())]
            for uid, name in users]
    rows.append([InlineKeyboardButton(
        text="⬅ Назад", callback_data=QstCb(a="card", id=idx).pack())])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def route_key_picker_keyboard(idx: int, options: list[str]) -> InlineKeyboardMarkup:
    """`options` — topic_key (route_by_topic) или название категории товара
    (route_by_category), уже отфильтрованные по активности."""
    rows = [[InlineKeyboardButton(text=opt, callback_data=QstCb(a="routekey", id=idx, k=opt).pack())]
            for opt in options]
    rows.append([InlineKeyboardButton(
        text="⬅ Назад", callback_data=QstCb(a="card", id=idx).pack())])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def route_user_picker_keyboard(idx: int, map_key: str,
                               users: list[tuple[int, str]]) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(
        text=name, callback_data=QstCb(a="routeset", id=idx, k=map_key, id2=uid).pack())]
        for uid, name in users]
    rows.append([InlineKeyboardButton(
        text="⬅ Назад", callback_data=QstCb(a="card", id=idx).pack())])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def cancel_keyboard(idx: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
        text="❌ Отменить", callback_data=QstCb(a="card", id=idx).pack())]])
