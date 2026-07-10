from bot.database.models import Role  # noqa: F401 — реэкспорт для handlers

PERMISSION_KEYS: frozenset[str] = frozenset({
    "tasks.manage", "users.manage", "schedules.manage", "articles.manage",
    "questions.manage", "reports.manage", "settings.manage", "topics.manage",
    "dictionaries.manage", "audit.view", "sync.run", "tasks.run_manual",
})

PERMISSION_TITLES: dict[str, str] = {
    "tasks.manage": "Управление задачами",
    "users.manage": "Управление пользователями",
    "schedules.manage": "Управление расписаниями",
    "articles.manage": "Управление артикулами",
    "questions.manage": "Управление вопросами",
    "reports.manage": "Управление отчетами",
    "settings.manage": "Глобальные настройки",
    "topics.manage": "Темы Telegram",
    "dictionaries.manage": "Справочники",
    "audit.view": "Просмотр аудита",
    "sync.run": "Запуск синхронизации",
    "tasks.run_manual": "Ручной запуск задач",
}
