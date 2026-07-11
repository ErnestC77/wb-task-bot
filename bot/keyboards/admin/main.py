from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


class AdminCb(CallbackData, prefix="ad"):
    s: str          # раздел
    a: str = "open"
    id: int = 0
    p: int = 1
    # Task 25 fix (обнаружено при тестировании UsrCb.pack()/.unpack(), см.
    # bot/keyboards/admin/users.py): aiogram трактует ЛЮБОЕ поле со значением
    # по умолчанию как "nullable" и на unpack() конвертирует пустую строку ""
    # в None (aiogram/filters/callback_data.py: _check_field_is_nullable
    # возвращает True, если у поля есть default, независимо от его типа) —
    # но pydantic тут же отклоняет None для типа `str`. Раньше это было
    # `k: str = ""`, и ЛЮБАЯ кнопка, где k остаётся дефолтным (например,
    # весь admin_menu_keyboard — AdminCb(s=code) без явного k, и "Назад" на
    # верхних уровнях, AdminCb(s=section, a="menu")), падала на
    # AdminCb.unpack() в реальном Telegram — т.е. был сломан вход в КАЖДЫЙ
    # пункт главного меню админ-панели. `str | None = None` — единственная
    # аннотация, реально совместимая с этим поведением aiogram (None
    # пакуется в "" через _encode_value, а unpack() восстанавливает "" -> None
    # для nullable-полей). См. test_admin_cb_default_k_roundtrips в
    # tests/test_admin_main.py.
    k: str | None = None


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
