# WB Task Bot

Telegram-бот для управления задачами команды Wildberries-бизнеса: повторяющиеся
задачи по расписанию, пакетная проверка артикулов, подтверждение owner/partner,
напоминания, недельный отчёт и полноценная админ-панель `/admin` внутри Telegram.

## Быстрый запуск (Docker)

1. Скопировать `.env.example` в `.env` и заполнить:
   - `BOT_TOKEN` — токен бота от @BotFather;
   - `DATABASE_URL` — оставить как есть для docker-compose;
   - `POSTGRES_USER`/`POSTGRES_PASSWORD`/`POSTGRES_DB`;
   - `GOOGLE_SHEETS_CREDENTIALS_FILE` — путь к service account JSON внутри контейнера
     (файл положить в `./credentials/service_account.json`, он монтируется автоматически);
   - `GOOGLE_SHEETS_SPREADSHEET_ID` — начальный ID таблицы (позже можно сменить в `/admin`);
   - `ENCRYPTION_KEY` — опционально, для будущего шифрования секретов в БД.
2. `docker compose up --build`
3. Бот применит миграции (`alembic upgrade head`, включая seed настроек и справочников)
   и запустится с восстановлением всех отложенных задач планировщика.
4. В личных сообщениях боту от владельца (Telegram ID должен быть заранее добавлен
   в таблицу `users` с ролью `owner` — см. «Первый запуск» ниже) выполнить `/admin`.

## Первый запуск — назначение owner

При первом запуске таблица `users` пуста. Добавить владельца вручную:

```bash
docker compose exec db psql -U wb_bot -d wb_task_bot -c \
  "INSERT INTO users (telegram_id, name, role, is_active) VALUES (<ваш_telegram_id>, 'Owner', 'owner', true);"
```

После этого можно синхронизировать остальных пользователей/темы/шаблоны задач
из Google Sheets через `/admin` → «🔄 Синхронизация Google Sheets» (сначала
«▶ Dry-run» для предпросмотра, затем «✅ Применить»).

## Локальная разработка без Docker

```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements-dev.txt   # Linux/macOS: .venv/bin/pip
docker compose up -d db                              # только Postgres
alembic upgrade head
pytest                                                # unit-тесты (SQLite)
python -m bot.main
```

## Тесты

- Unit: `pytest` (SQLite in-memory, без внешних зависимостей).
- Интеграционные (PostgreSQL): см. Task 38 плана реализации.

## Команды бота

`/start`, `/help`, `/cancel` — базовые; `/today`, `/my_tasks`, `/overdue` —
списки задач; `/report` — недельный отчёт по запросу; `/admin` — админ-панель
(доступ по роли/выданным правам).

## Структура проекта

- `bot/handlers` — хендлеры aiogram (в т.ч. `bot/handlers/admin/*` — 16 разделов
  админ-панели);
- `bot/services` — бизнес-логика (TaskService, DeliveryService, SchedulerService,
  ApprovalService, ArticleCheckService, QuestionService, ReportService,
  GoogleSheetsService и др.);
- `bot/database` — SQLAlchemy-модели, репозитории, Alembic-миграции;
- `bot/keyboards` — inline-клавиатуры (в т.ч. `bot/keyboards/admin/*`);
- `bot/states` — FSM-состояния;
- `bot/utils` — общие утилиты (валидация, HTML-экранирование, права, время).

## Админ-панель

`/admin` — полное управление ботом внутри Telegram: настройки, пользователи, темы,
шаблоны задач, расписания, артикулы, справочники, маршрутизация вопросов,
напоминания/подтверждение, отчёты, синхронизация с Google Sheets, ручной запуск
и активные задачи, журнал изменений, резервные операции. Доступ: `owner` —
полный, `partner` — по выданным правам (`manager_wb`/`logistic` в админ-панель
доступа не имеют).

## Что не входит в MVP

- Подключение WB API (заложено место в `bot/services/wb_analytics_service.py` —
  пороговые значения из ТЗ, без реальных запросов к API).
- Отдельный веб-интерфейс — управление только через Telegram.
