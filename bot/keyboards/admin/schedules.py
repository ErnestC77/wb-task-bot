"""Клавиатуры раздела админ-панели «📅 Расписания» (Task 28).

`SchCb` — отдельный `CallbackData`-класс с префиксом "sc" для навигации внутри
раздела (список активных шаблонов с расписанием -> карточка расписания ->
список редактируемых полей расписания, whitelist по индексу -> FSM/toggle
редактирования одного поля), по образцу `CfgCb` (Task 27) / `UsrCb` (Task 25) /
`TopCb` (Task 26).

Бриф Task 28 задаёт интерфейс `SchCb`: `a: str`, `id: int = 0`, `k: str = ""`
(без поля пагинации). Два отклонения от буквального интерфейса, оба —
повторение уже устоявшихся, подтверждённых тестами паттернов предыдущих
разделов, а не догадка:

1. `k: str = ""` заменено на `k: str | None = None` — системный баг Task 24
   (aiogram трактует ЛЮБОЕ поле со значением по умолчанию как nullable и на
   unpack() подменяет пустую строку на None независимо от типа поля; pydantic
   отклоняет None для не-Optional `str`). `id: int = 0` оставлен как есть —
   целочисленные default безопасны (str(0) != "").
2. Добавлено поле `p: int = 1` (пагинация списка) — без него список активных
   шаблонов с расписанием не может быть постранично пролистан при их большом
   числе; тот же паттерн, что `CfgCb.p`/`UsrCb.p`/`TopCb.p` во всех предыдущих
   разделах.

Каждая клавиатура ниже покрыта тестом, реально вызывающим `.pack()`/`.unpack()`
(см. tests/test_admin_schedules.py).
"""
from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.keyboards.admin.main import AdminCb


class SchCb(CallbackData, prefix="sc"):
    a: str
    id: int = 0
    p: int = 1
    k: str | None = None


def schedules_list_keyboard(entries: list[tuple[int, str]], page: int,
                            total_pages: int) -> InlineKeyboardMarkup:
    """`entries` — [(config_id, отображаемая строка), ...] уже одной страницы
    (только активные шаблоны — см. `_show_schedules_list`)."""
    rows = [[InlineKeyboardButton(text=label, callback_data=SchCb(a="card", id=config_id).pack())]
            for config_id, label in entries]
    if total_pages > 1:
        rows.append([
            InlineKeyboardButton(text="⬅", callback_data=SchCb(
                a="list", p=max(1, page - 1)).pack()),
            InlineKeyboardButton(text=f"{page}/{total_pages}",
                                 callback_data=SchCb(a="noop").pack()),
            InlineKeyboardButton(text="➡", callback_data=SchCb(
                a="list", p=min(total_pages, page + 1)).pack()),
        ])
    rows.append([InlineKeyboardButton(
        text="⬅ Назад", callback_data=AdminCb(s="sch", a="menu").pack())])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def schedule_card_keyboard(config_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏ Изменить поле расписания",
                              callback_data=SchCb(a="fields", id=config_id, p=1).pack())],
        [InlineKeyboardButton(text="⬅ Назад", callback_data=SchCb(a="list", p=1).pack())],
    ])


def schedule_fields_keyboard(config_id: int, entries: list[tuple[int, str]], page: int,
                             total_pages: int) -> InlineKeyboardMarkup:
    """`entries` — [(index_в_SCHEDULE_FIELD_LIST, "Поле = значение"), ...] одной
    страницы. Индекс уходит в `SchCb.k` строкой (`k=str(idx)`), config_id —
    в отдельное поле `id` (тот же паттерн, что `fields_list_keyboard` Task 27)."""
    rows = [[InlineKeyboardButton(
        text=label, callback_data=SchCb(a="editfield", id=config_id, k=str(idx)).pack())]
        for idx, label in entries]
    if total_pages > 1:
        rows.append([
            InlineKeyboardButton(text="⬅", callback_data=SchCb(
                a="fields", id=config_id, p=max(1, page - 1)).pack()),
            InlineKeyboardButton(text=f"{page}/{total_pages}",
                                 callback_data=SchCb(a="noop").pack()),
            InlineKeyboardButton(text="➡", callback_data=SchCb(
                a="fields", id=config_id, p=min(total_pages, page + 1)).pack()),
        ])
    rows.append([InlineKeyboardButton(
        text="⬅ Назад", callback_data=SchCb(a="card", id=config_id).pack())])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def schedule_type_choice_keyboard(config_id: int, values: list[str]) -> InlineKeyboardMarkup:
    """Выбор `schedule_type` кнопками (закрытый список `ScheduleType`) — значение
    уходит в `SchCb.k` строкой, config_id остаётся в отдельном поле `id`."""
    rows = [[InlineKeyboardButton(
        text=value, callback_data=SchCb(a="settype", id=config_id, k=value).pack())]
        for value in values]
    rows.append([InlineKeyboardButton(
        text="⬅ Назад", callback_data=SchCb(a="card", id=config_id).pack())])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def weekday_choice_keyboard(config_id: int) -> InlineKeyboardMarkup:
    """Выбор дня недели (0=понедельник..6=воскресенье) кнопками — для
    `schedule_value` при `schedule_type=weekly`."""
    labels = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
    rows = [[InlineKeyboardButton(
        text=labels[i], callback_data=SchCb(a="setweekday", id=config_id, k=str(i)).pack())]
        for i in range(7)]
    rows.append([InlineKeyboardButton(
        text="⬅ Назад", callback_data=SchCb(a="card", id=config_id).pack())])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def cancel_edit_keyboard(config_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
        text="❌ Отменить", callback_data=SchCb(a="card", id=config_id).pack())]])
