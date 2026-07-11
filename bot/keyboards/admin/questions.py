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
поле). `k: str | None = None` — безопасный ТОКЕН выбранной темы/категории для
маршрутов (topic_key для route_by_topic — не редактируется через админку и
безопасен как есть; `str(category.id)` для route_by_category — НЕ имя
категории напрямую, т.к. имя редактируется свободным текстом в Task 30 и
может содержать `:`, что ломает `.pack()`; см. `route_key_picker_keyboard`).
Бизнес-ключ (имя категории) резолвится из токена обратно в
`bot/handlers/admin/questions.py` только на уровне вызова `update_route_map`,
никогда не кладётся в CallbackData напрямую. НЕ `str = ""` (системный баг
Tasks 24-25 — aiogram трактует ЛЮБОЕ поле со значением по умолчанию как
nullable и подменяет пустую строку на `None` при `unpack()`, что pydantic
отклоняет для не-Optional `str`).
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


def route_key_picker_keyboard(idx: int, options: list[tuple[str, str]]) -> InlineKeyboardMarkup:
    """`options` — [(token, label), ...], уже отфильтрованные по активности.
    Для route_by_topic token == label == topic_key (не редактируется через
    админку — безопасен как есть). Для route_by_category token —
    `str(category.id)`, НЕ имя категории: имя редактируется свободным текстом
    (Task 30) и может содержать `:`, что ломает `QstCb.pack()` (regression,
    Task 31 review). Бизнес-ключ (имя категории) резолвится из id обратно
    только на уровне `update_route_map`, не в CallbackData."""
    rows = [[InlineKeyboardButton(text=label, callback_data=QstCb(a="routekey", id=idx, k=token).pack())]
            for token, label in options]
    rows.append([InlineKeyboardButton(
        text="⬅ Назад", callback_data=QstCb(a="card", id=idx).pack())])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def route_user_picker_keyboard(idx: int, token: str,
                               users: list[tuple[int, str]]) -> InlineKeyboardMarkup:
    """`token` — тот же безопасный токен, что был выбран на шаге `routekey`
    (topic_key либо `str(category.id)`), переносится дальше в `routeset`."""
    rows = [[InlineKeyboardButton(
        text=name, callback_data=QstCb(a="routeset", id=idx, k=token, id2=uid).pack())]
        for uid, name in users]
    rows.append([InlineKeyboardButton(
        text="⬅ Назад", callback_data=QstCb(a="card", id=idx).pack())])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def cancel_keyboard(idx: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
        text="❌ Отменить", callback_data=QstCb(a="card", id=idx).pack())]])
