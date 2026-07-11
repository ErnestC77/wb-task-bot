"""Клавиатуры раздела админ-панели «✅ Шаблоны задач» (Task 27).

`CfgCb` — ОТДЕЛЬНЫЙ (не `AdminCb`) `CallbackData`-класс с префиксом "tc" для
всей навигации ВНУТРИ раздела (список шаблонов -> карточка -> список
редактируемых полей (whitelist, по индексу) -> FSM редактирования одного поля
-> toggle активности -> клонирование -> ручной запуск -> мастер создания),
по образцу `UsrCb` (Task 25) / `TopCb` (Task 26). Вход в раздел (первый тап из
главного меню админ-панели и «Назад» с самого верхнего уровня) остаётся на
`AdminCb(s="cfg")` и идёт через `resolve_admin`/`handle_section` как обычно;
но кнопки этого модуля используют СВОЙ `CfgCb`, матчатся собственным
`@router.callback_query(CfgCb.filter())` в bot/handlers/admin/task_configs.py
и НЕ проходят через `resolve_admin` — поэтому каждый такой callback обязан
сам проверить actor + право tasks.manage (см. `_resolve_actor` в handlers).

КРИТИЧЕСКИ ВАЖНО (системный урок Task 24, Critical-баг, подтверждённый снова
в Task 25/26): ни в одно поле `CallbackData` НИКОГДА не склеивать два значения
через ":" (или любой другой разделитель) — aiogram `CallbackData.pack()`
резервирует ":" как разделитель ПОЛЕЙ. Каждое отдельное составное значение
(id шаблона, индекс поля в EDITABLE_FIELDS, значение выбора мастера создания)
уходит в СВОЁ типизированное поле.

ВАЖНО (тот же баг, вторая форма — Task 25 fix, подтверждён в Task 26): брифовая
сигнатура `CfgCb(...): k: str = ""` НЕ используется буквально — используется
`k: str | None = None`. aiogram трактует ЛЮБОЕ поле со значением по умолчанию
как "nullable" и на `unpack()` подменяет пустую строку "" на None (независимо
от типа поля), а pydantic тут же отклоняет None для `str`. `id: int = 0` и
`p: int = 1` безопасны без изменений — это тот же паттерн, что уже работает в
проде для `AdminCb.id`/`AdminCb.p`, `TopCb.id`/`TopCb.p` (баг был именно в
default `str = ""`, не в целочисленных default). Каждая клавиатура ниже
покрыта тестом, реально вызывающим `.pack()`/`.unpack()` — см.
tests/test_admin_task_configs.py.
"""
from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.keyboards.admin.main import AdminCb


class CfgCb(CallbackData, prefix="tc"):
    a: str
    id: int = 0
    p: int = 1
    k: str | None = None


def configs_list_keyboard(entries: list[tuple[int, str]], page: int,
                          total_pages: int) -> InlineKeyboardMarkup:
    """`entries` — [(config_id, отображаемая_строка), ...] уже одной страницы.
    config_id (PK TaskConfig.id) уходит в отдельное поле CfgCb.id (int) —
    атомарное значение, не часть склеенной строки."""
    rows = [[InlineKeyboardButton(text=label, callback_data=CfgCb(a="card", id=config_id).pack())]
            for config_id, label in entries]
    rows.append([InlineKeyboardButton(
        text="➕ Создать шаблон", callback_data=CfgCb(a="create").pack())])
    if total_pages > 1:
        rows.append([
            InlineKeyboardButton(text="⬅", callback_data=CfgCb(
                a="list", p=max(1, page - 1)).pack()),
            InlineKeyboardButton(text=f"{page}/{total_pages}",
                                 callback_data=CfgCb(a="noop").pack()),
            InlineKeyboardButton(text="➡", callback_data=CfgCb(
                a="list", p=min(total_pages, page + 1)).pack()),
        ])
    rows.append([InlineKeyboardButton(
        text="⬅ Назад", callback_data=AdminCb(s="cfg", a="menu").pack())])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def config_card_keyboard(config_id: int, is_active: bool) -> InlineKeyboardMarkup:
    toggle_text = "🚫 Деактивировать" if is_active else "✅ Активировать"
    rows = [
        [InlineKeyboardButton(text="✏ Изменить поле",
                              callback_data=CfgCb(a="fields", id=config_id, p=1).pack())],
        [InlineKeyboardButton(text=toggle_text,
                              callback_data=CfgCb(a="toggle", id=config_id).pack())],
        [InlineKeyboardButton(text="📋 Клонировать",
                              callback_data=CfgCb(a="clone", id=config_id).pack())],
        [InlineKeyboardButton(text="▶ Запустить сейчас",
                              callback_data=CfgCb(a="run", id=config_id).pack())],
        [InlineKeyboardButton(text="⬅ Назад", callback_data=CfgCb(a="list", p=1).pack())],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def fields_list_keyboard(config_id: int, entries: list[tuple[int, str]], page: int,
                         total_pages: int) -> InlineKeyboardMarkup:
    """`entries` — [(index_в_FIELD_LIST_FOR_UI, отображаемая_строка "Поле = значение"), ...]
    уже одной страницы. Индекс уходит в CfgCb.k строкой (`k=str(idx)`) — config_id
    остаётся в отдельном поле `id`, ровно тот же паттерн, что `settings_list_keyboard`
    (Task 24, AdminCb.k=category/id=idx) и `permission_checklist_keyboard` (Task 25,
    UsrCb.id=user_id/k=str(idx))."""
    rows = [[InlineKeyboardButton(
        text=label, callback_data=CfgCb(a="editfield", id=config_id, k=str(idx)).pack())]
        for idx, label in entries]
    if total_pages > 1:
        rows.append([
            InlineKeyboardButton(text="⬅", callback_data=CfgCb(
                a="fields", id=config_id, p=max(1, page - 1)).pack()),
            InlineKeyboardButton(text=f"{page}/{total_pages}",
                                 callback_data=CfgCb(a="noop").pack()),
            InlineKeyboardButton(text="➡", callback_data=CfgCb(
                a="fields", id=config_id, p=min(total_pages, page + 1)).pack()),
        ])
    rows.append([InlineKeyboardButton(
        text="⬅ Назад", callback_data=CfgCb(a="card", id=config_id).pack())])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def creation_choice_keyboard(action: str, options: list[tuple[str, str]]) -> InlineKeyboardMarkup:
    """Универсальная клавиатура выбора одного значения на шаге мастера создания
    (`action` — код шага: "cr_scn"/"cr_sched"/"cr_appr"/"cr_resp"/"cr_topic"/
    "cr_recv"; `options` — [(значение_для_k, отображаемая_подпись), ...]).
    Значение уходит в CfgCb.k строкой — id остаётся default (0, шаблон ещё не
    создан) — ОДНО атомарное значение на кнопку, не склейка."""
    rows = [[InlineKeyboardButton(text=label, callback_data=CfgCb(a=action, k=value).pack())]
            for value, label in options]
    rows.append([InlineKeyboardButton(
        text="❌ Отменить создание", callback_data=CfgCb(a="cr_cancel").pack())])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def cancel_creation_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
        text="❌ Отменить создание", callback_data=CfgCb(a="cr_cancel").pack())]])
