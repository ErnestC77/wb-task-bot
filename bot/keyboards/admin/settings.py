from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.keyboards.admin.main import AdminCb

# Заголовки категорий для отображения — чисто декоративно, ключ категории
# (используемый в callback_data и SETTINGS_REGISTRY) не меняется.
CATEGORY_TITLES: dict[str, str] = {
    "general": "Общие",
    "article_check": "Проверка артикулов",
    "approval": "Подтверждение",
    "reminders": "Напоминания",
    "questions": "Вопросы",
    "reports": "Отчеты",
    "sync": "Синхронизация Google Sheets",
    "delivery_log": "Журнал отправок",
    "status_notifications": "Уведомления о статусах",
}


def categories_keyboard(section: str, categories: list[str]) -> InlineKeyboardMarkup:
    """Список категорий. `section` — код раздела меню ("set" или "rem"), под
    которым эта клавиатура была открыта; кнопка «Назад» ведёт в главное меню
    админ-панели (a="menu") под ТЕМ ЖЕ section, чтобы право проверялось
    единообразно (SECTION_ALIASES уже сводит и "set", и "rem" к
    settings.manage). Дальнейшая навигация по категориям всегда идёт через
    s="set" — раздел "rem" лишь другая точка входа к тем же карточкам."""
    rows = [[InlineKeyboardButton(
        text=CATEGORY_TITLES.get(cat, cat),
        callback_data=AdminCb(s="set", a="cat", k=cat, p=1).pack())]
        for cat in categories]
    rows.append([InlineKeyboardButton(
        text="⬅ Назад", callback_data=AdminCb(s=section, a="menu").pack())])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def settings_list_keyboard(category: str, entries: list[tuple[int, str, str]],
                           page: int, total_pages: int) -> InlineKeyboardMarkup:
    """`entries` — [(index_в_registry, key, отображаемый_текст), ...] уже одной
    страницы. В callback_data уходит ИНДЕКС отдельным полем (`a="card", k=category,
    id=idx`) — НЕ конкатенацией строкой через ":" (тот разделитель зарезервирован
    aiogram `CallbackData.pack()` и ломает pack() на любом значении с ":" внутри
    поля, см. Task 24 review). Сервер восстанавливает ключ через `key_by_index`
    (whitelist, см. bot/handlers/admin/settings.py)."""
    rows = [[InlineKeyboardButton(
        text=label, callback_data=AdminCb(s="set", a="card", k=category, id=idx).pack())]
        for idx, _key, label in entries]
    if total_pages > 1:
        rows.append([
            InlineKeyboardButton(text="⬅", callback_data=AdminCb(
                s="set", a="cat", k=category, p=max(1, page - 1)).pack()),
            InlineKeyboardButton(text=f"{page}/{total_pages}",
                                 callback_data=AdminCb(s="set", a="noop").pack()),
            InlineKeyboardButton(text="➡", callback_data=AdminCb(
                s="set", a="cat", k=category, p=min(total_pages, page + 1)).pack()),
        ])
    rows.append([InlineKeyboardButton(
        text="⬅ Назад", callback_data=AdminCb(s="set", a="open").pack())])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def setting_card_keyboard(category: str, idx: int) -> InlineKeyboardMarkup:
    """Индекс уходит в отдельное поле AdminCb.id, категория — в k (см. комментарий
    в settings_list_keyboard про запрет ":" внутри значения поля CallbackData)."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏ Изменить",
                              callback_data=AdminCb(s="set", a="edit", k=category, id=idx).pack())],
        [InlineKeyboardButton(text="↩ Сбросить к default",
                              callback_data=AdminCb(s="set", a="reset", k=category, id=idx).pack())],
        [InlineKeyboardButton(text="⬅ Назад",
                              callback_data=AdminCb(s="set", a="cat", k=category, p=1).pack())],
    ])
