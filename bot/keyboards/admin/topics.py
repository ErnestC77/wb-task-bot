"""Клавиатуры раздела админ-панели «🗂 Темы Telegram» (Task 26).

`TopCb` — ОТДЕЛЬНЫЙ (не `AdminCb`) `CallbackData`-класс с префиксом "tp" для
всей навигации ВНУТРИ раздела (список -> карточка -> FSM редактирования
названия/message_thread_id/типа событий -> toggle активности -> ручная
тестовая отправка), по образцу `UsrCb` (Task 25) и `AdminCb.k`/`id`-паттерна
из "Настроек" (Task 24). Вход в раздел (первый тап из главного меню
админ-панели и «Назад» с самого верхнего уровня) остаётся на
`AdminCb(s="top")` и идёт через `resolve_admin`/`handle_section` как обычно;
но кнопки этого модуля используют СВОЙ `TopCb`, матчатся собственным
`@router.callback_query(TopCb.filter())` в bot/handlers/admin/topics.py и НЕ
проходят через `resolve_admin` — поэтому каждый такой callback обязан сам
проверить actor + право topics.manage (см. `_resolve_actor` в handlers).

В отличие от `UsrCb`, здесь НЕ нужно составное строковое поле `k`: каждая
операция (изменить название/thread_id/тип событий, toggle, тест) адресуется
ОДНИМ атомарным `id` — первичным ключом `Topic.id` (автоинкремент из БД,
как `user_id` в `UsrCb`), а различие между "что редактируем" кодируется
РАЗНЫМИ значениями поля `a` ("editname"/"editthread"/"editevents"), а не
склейкой двух значений в одно поле. Как и везде: aiogram `CallbackData.pack()`
резервирует ":" как разделитель полей — ни в одно поле `TopCb` ":" никогда не
попадёт, потому что мы нигде не конкатенируем два значения строкой.

Task 24/25 lesson про nullable-поля здесь неприменим впрямую: у `TopCb` нет
строковых полей со значением по умолчанию (`a` обязателен, `id: int = 0` и
`p: int = 1` — целочисленные с default, ровно тот же паттерн, что уже
работает в проде для `AdminCb.id`/`AdminCb.p` — см. docstring `AdminCb`:
баг был именно в default `str = ""`, где aiogram подменяет "" на None при
unpack(), а НЕ в целочисленных полях, которые никогда не сериализуются в
пустую строку). Каждая клавиатура ниже покрыта тестом, реально вызывающим
`.pack()`/`.unpack()` — см. tests/test_admin_topics.py.
"""
from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.keyboards.admin.main import AdminCb


class TopCb(CallbackData, prefix="tp"):
    a: str
    id: int = 0
    p: int = 1


def topics_list_keyboard(entries: list[tuple[int, str]], page: int,
                         total_pages: int) -> InlineKeyboardMarkup:
    """`entries` — [(topic_id, отображаемая_строка), ...] уже одной страницы.
    topic_id (PK Topic.id) уходит в отдельное поле TopCb.id (int) — атомарное
    значение, не часть склеенной строки."""
    rows = [[InlineKeyboardButton(text=label, callback_data=TopCb(a="card", id=topic_id).pack())]
            for topic_id, label in entries]
    if total_pages > 1:
        rows.append([
            InlineKeyboardButton(text="⬅", callback_data=TopCb(
                a="list", p=max(1, page - 1)).pack()),
            InlineKeyboardButton(text=f"{page}/{total_pages}",
                                 callback_data=TopCb(a="noop").pack()),
            InlineKeyboardButton(text="➡", callback_data=TopCb(
                a="list", p=min(total_pages, page + 1)).pack()),
        ])
    rows.append([InlineKeyboardButton(
        text="⬅ Назад", callback_data=AdminCb(s="top", a="menu").pack())])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def topic_card_keyboard(topic_id: int, is_active: bool) -> InlineKeyboardMarkup:
    toggle_text = "🚫 Деактивировать" if is_active else "✅ Активировать"
    rows = [
        [InlineKeyboardButton(text="✏ Название",
                              callback_data=TopCb(a="editname", id=topic_id).pack())],
        [InlineKeyboardButton(text="🔀 Message thread ID",
                              callback_data=TopCb(a="editthread", id=topic_id).pack())],
        [InlineKeyboardButton(text="📋 Тип событий",
                              callback_data=TopCb(a="editevents", id=topic_id).pack())],
        [InlineKeyboardButton(text=toggle_text,
                              callback_data=TopCb(a="toggle", id=topic_id).pack())],
        [InlineKeyboardButton(text="📨 Тестовая отправка",
                              callback_data=TopCb(a="test", id=topic_id).pack())],
        [InlineKeyboardButton(text="⬅ Назад", callback_data=TopCb(a="list", p=1).pack())],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)
