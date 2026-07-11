"""Сборка роутера админ-панели.

`admin_router` объединяет `main.router` (каркас Task 23: /admin, RBAC-фильтр,
пагинация, подтверждения) и роутеры конкретных разделов, которые
добавляются в Tasks 24-27.

`SECTION_HANDLERS` — реестр диспетчеризации по коду раздела AdminCb.s
(например "usr", "art"...). Модули разделов регистрируют себя сюда при
импорте: `SECTION_HANDLERS["usr"] = show_users_section`. Пока раздел не
реализован, `handle_section` в main.py отвечает «Раздел в разработке».
"""
from aiogram import Router

SECTION_HANDLERS: dict[str, callable] = {}

admin_router = Router(name=__name__)

from bot.handlers.admin import main as _main  # noqa: E402 — после объявления SECTION_HANDLERS

admin_router.include_router(_main.router)

# Роутеры разделов (Tasks 24-27) подключаются здесь по мере реализации.

from bot.handlers.admin import settings as _settings  # noqa: E402

admin_router.include_router(_settings.router)
SECTION_HANDLERS["set"] = _settings.handle_settings_section
SECTION_HANDLERS["rem"] = _settings.handle_reminders_section
