"""Клавиатуры раздела админ-панели «👥 Пользователи и роли» (Task 25).

`UsrCb` — ОТДЕЛЬНЫЙ (не `AdminCb`) `CallbackData`-класс с префиксом "u" для
всей навигации ВНУТРИ раздела (список -> карточка -> FSM добавления ->
роль -> toggle прав -> чек-лист прав -> переназначение задачи -> проверка
личных сообщений). Вход в раздел (первый тап из главного меню админ-панели
и кнопка «Назад» с самого верхнего уровня) остаётся на `AdminCb(s="usr")` и
идёт через `resolve_admin`/`handle_section` как обычно; но т.к. кнопки этого
модуля используют СВОЙ `UsrCb`, они матчатся собственным
`@router.callback_query(UsrCb.filter())` в bot/handlers/admin/users.py и НЕ
проходят через `resolve_admin` — поэтому каждый такой callback обязан сам
проверить actor + право users.manage (см. `_resolve_actor` в handlers).

КРИТИЧЕСКИ ВАЖНО (системный урок Task 24, Critical-баг): ни в `AdminCb`, ни
в `UsrCb` НИКОГДА не склеивать два значения в одно строковое поле через ":"
(или любой другой разделитель) — aiogram `CallbackData.pack()` резервирует
":" как разделитель ПОЛЕЙ, и любое значение поля, содержащее ":", ломает
`.pack()`. Вместо этого каждое отдельное составное значение (id пользователя,
индекс права в sorted(PERMISSION_KEYS), id задачи) уходит в СВОЁ типизированное
поле (`id: int`, `k: str` как одно атомарное значение — например,
`k=str(idx)`, а не `k=f"{user_id}:{idx}"`). Каждая новая клавиатура ниже
покрыта тестом, реально вызывающим `.pack()`/`.unpack()` — см.
tests/test_admin_users.py.

ВТОРОЙ найденный при тестировании класс бага (не из брифа, но той же
природы — "выглядит рабочим, но .pack()/.unpack() реально падает"):
aiogram считает ЛЮБОЕ поле со значением по умолчанию "nullable" и на
unpack() подменяет пустую строку "" на None (независимо от типа поля), а
pydantic тут же отклоняет None для `str`. Поэтому `k` — `str | None = None`,
а НЕ `str = ""` (та же правка внесена в `AdminCb.k`, см. комментарий там —
без неё был сломан ВЕСЬ вход в админ-меню, а не только раздел users).
"""
from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.database.models import Role
from bot.keyboards.admin.main import AdminCb
from bot.utils.permissions import PERMISSION_TITLES


class UsrCb(CallbackData, prefix="u"):
    a: str
    id: int = 0
    p: int = 1
    k: str | None = None


ROLE_TITLES: dict[str, str] = {
    Role.OWNER: "Владелец",
    Role.PARTNER: "Партнёр",
    Role.MANAGER_WB: "Менеджер WB",
    Role.LOGISTIC: "Логист",
}


def users_list_keyboard(entries: list[tuple[int, str]], page: int,
                        total_pages: int) -> InlineKeyboardMarkup:
    """`entries` — [(user_id, отображаемая_строка), ...] уже одной страницы.
    user_id уходит в отдельное поле UsrCb.id (int) — атомарное значение, не
    часть склеенной строки."""
    rows = [[InlineKeyboardButton(text=label, callback_data=UsrCb(a="card", id=user_id).pack())]
            for user_id, label in entries]
    rows.append([InlineKeyboardButton(
        text="➕ Добавить пользователя", callback_data=UsrCb(a="add").pack())])
    if total_pages > 1:
        rows.append([
            InlineKeyboardButton(text="⬅", callback_data=UsrCb(
                a="list", p=max(1, page - 1)).pack()),
            InlineKeyboardButton(text=f"{page}/{total_pages}",
                                 callback_data=UsrCb(a="noop").pack()),
            InlineKeyboardButton(text="➡", callback_data=UsrCb(
                a="list", p=min(total_pages, page + 1)).pack()),
        ])
    rows.append([InlineKeyboardButton(
        text="⬅ Назад", callback_data=AdminCb(s="usr", a="menu").pack())])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def user_card_keyboard(user_id: int, is_active: bool,
                       show_permissions: bool) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="✏ Имя",
                              callback_data=UsrCb(a="editname", id=user_id).pack()),
         InlineKeyboardButton(text="✏ Username",
                              callback_data=UsrCb(a="editun", id=user_id).pack())],
        [InlineKeyboardButton(text="🎭 Роль",
                              callback_data=UsrCb(a="rolepick", id=user_id).pack())],
    ]
    if show_permissions:
        rows.append([InlineKeyboardButton(
            text="🔑 Права", callback_data=UsrCb(a="perm", id=user_id).pack())])
    toggle_text = "🚫 Деактивировать" if is_active else "✅ Активировать"
    rows.append([InlineKeyboardButton(
        text=toggle_text, callback_data=UsrCb(a="toggle", id=user_id).pack())])
    rows.append([InlineKeyboardButton(
        text="📋 Открытые задачи", callback_data=UsrCb(a="reassign", id=user_id, p=1).pack())])
    rows.append([InlineKeyboardButton(
        text="✉ Проверить личку", callback_data=UsrCb(a="checkpm", id=user_id).pack())])
    rows.append([InlineKeyboardButton(
        text="⬅ Назад", callback_data=UsrCb(a="list", p=1).pack())])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def role_picker_keyboard(user_id: int) -> InlineKeyboardMarkup:
    """`user_id=0` — часть FSM добавления (пользователь ещё не создан, данные
    в FSM state); `user_id>0` — смена роли существующего пользователя (id из
    БД, автоинкремент с 1, поэтому 0 однозначно означает «добавление»)."""
    rows = [[InlineKeyboardButton(
        text=title, callback_data=UsrCb(a="role", id=user_id, k=role).pack())]
        for role, title in ROLE_TITLES.items()]
    back = UsrCb(a="add") if user_id == 0 else UsrCb(a="card", id=user_id)
    rows.append([InlineKeyboardButton(text="⬅ Назад", callback_data=back.pack())])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def permission_checklist_keyboard(user_id: int, sorted_keys: list[str],
                                  perms: dict[str, bool]) -> InlineKeyboardMarkup:
    """Индекс права в `sorted_keys` (== sorted(PERMISSION_KEYS)) уходит в
    UsrCb.k строкой (`k=str(idx)`) — атомарное значение поля, НЕ конкатенация
    с user_id (тот остаётся в отдельном поле `id`). Ровно тот же паттерн, что
    `id=idx` в bot/keyboards/admin/settings.py, только числовой индекс здесь
    хранится строкой в `k`, а не в `id`, потому что `id` уже занят user_id."""
    rows = []
    for idx, key in enumerate(sorted_keys):
        mark = "✅" if perms.get(key) else "▫"
        title = PERMISSION_TITLES.get(key, key)
        rows.append([InlineKeyboardButton(
            text=f"{mark} {title}",
            callback_data=UsrCb(a="toggle", id=user_id, k=str(idx)).pack())])
    rows.append([InlineKeyboardButton(
        text="⬅ Назад", callback_data=UsrCb(a="card", id=user_id).pack())])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def reassign_task_list_keyboard(user_id: int, entries: list[tuple[int, str]], page: int,
                                total_pages: int) -> InlineKeyboardMarkup:
    """`entries` — [(instance_id, отображаемая_строка), ...]. `k=str(user_id)`
    несёт «чью открытую задачу мы переназначаем» отдельным атомарным полем —
    нужно кнопке «Назад» и следующему шагу (`reassign_target_keyboard`), а не
    склеено с instance_id (тот в отдельном поле `id`)."""
    rows = [[InlineKeyboardButton(
        text=label, callback_data=UsrCb(a="reassign_pick", id=inst_id, k=str(user_id)).pack())]
        for inst_id, label in entries]
    if total_pages > 1:
        rows.append([
            InlineKeyboardButton(text="⬅", callback_data=UsrCb(
                a="reassign", id=user_id, p=max(1, page - 1)).pack()),
            InlineKeyboardButton(text=f"{page}/{total_pages}",
                                 callback_data=UsrCb(a="noop").pack()),
            InlineKeyboardButton(text="➡", callback_data=UsrCb(
                a="reassign", id=user_id, p=min(total_pages, page + 1)).pack()),
        ])
    rows.append([InlineKeyboardButton(
        text="⬅ Назад", callback_data=UsrCb(a="card", id=user_id).pack())])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def reassign_target_keyboard(instance_id: int, origin_user_id: int,
                             candidates: list[tuple[int, str]]) -> InlineKeyboardMarkup:
    """`instance_id` — отдельное поле `id`; id нового ответственного — отдельное
    поле `k` (строкой, `k=str(new_user_id)`) — снова два атомарных значения в
    двух полях, а не одна склеенная строка."""
    rows = [[InlineKeyboardButton(
        text=label,
        callback_data=UsrCb(a="reassign_do", id=instance_id, k=str(new_user_id)).pack())]
        for new_user_id, label in candidates]
    rows.append([InlineKeyboardButton(
        text="⬅ Назад",
        callback_data=UsrCb(a="reassign", id=origin_user_id, p=1).pack())])
    return InlineKeyboardMarkup(inline_keyboard=rows)
