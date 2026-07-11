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

from bot.handlers.admin import users as _users  # noqa: E402

admin_router.include_router(_users.router)
SECTION_HANDLERS["usr"] = _users.handle_users_section

from bot.handlers.admin import topics as _topics  # noqa: E402

admin_router.include_router(_topics.router)
SECTION_HANDLERS["top"] = _topics.handle_topics_section

from bot.handlers.admin import task_configs as _task_configs  # noqa: E402

admin_router.include_router(_task_configs.router)
SECTION_HANDLERS["cfg"] = _task_configs.handle_configs_section

from bot.handlers.admin import schedules as _schedules  # noqa: E402

admin_router.include_router(_schedules.router)
SECTION_HANDLERS["sch"] = _schedules.handle_schedules_section

from bot.handlers.admin import articles as _articles  # noqa: E402

admin_router.include_router(_articles.router)
SECTION_HANDLERS["art"] = _articles.handle_articles_section
