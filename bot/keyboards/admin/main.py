from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


class AdminCb(CallbackData, prefix="ad"):
    s: str          # раздел
    a: str = "open"
    id: int = 0
    p: int = 1
    k: str = ""


MENU_ITEMS: list[tuple[str, str]] = [
    ("set", "⚙ Настройки"),
    ("usr", "👥 Пользователи и роли"),
    ("top", "🗂 Темы Telegram"),
    ("cfg", "✅ Шаблоны задач"),
    ("sch", "📅 Расписания"),
    ("art", "📦 Артикулы"),
    ("dic", "⚠ Категории проблем"),
    ("dic2", "🛠 Варианты решений"),
    ("qst", "❓ Маршрутизация вопросов"),
    ("rem", "🔔 Напоминания и подтверждение"),
    ("rep", "📊 Отчеты"),
    ("syn", "🔄 Синхронизация Google Sheets"),
    ("run", "▶ Ручной запуск"),
    ("act", "📋 Активные задачи"),
    ("aud", "🧾 Журнал изменений"),
    ("bak", "💾 Резервные операции"),
]

# алиасы пунктов меню на разделы прав
SECTION_ALIASES = {"dic2": "dic", "rem": "set", "run": "ops", "act": "ops", "bak": "ops"}


def admin_menu_keyboard(allowed: set[str]) -> InlineKeyboardMarkup:
    rows = []
    for code, title in MENU_ITEMS:
        section = SECTION_ALIASES.get(code, code)
        if section in allowed:
            rows.append([InlineKeyboardButton(
                text=title, callback_data=AdminCb(s=code).pack())])
    return InlineKeyboardMarkup(inline_keyboard=rows)
