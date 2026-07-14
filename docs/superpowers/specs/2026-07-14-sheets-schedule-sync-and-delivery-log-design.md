# Пересинхронизация расписания + Журнал отправок в Google Sheets — дизайн

**Дата:** 2026-07-14
**Статус:** утверждён пользователем в диалоге, ожидает записи в implementation-плане

## Контекст и проблема

Пользователь сообщил, что все задания отправились разом (несколько сообщений в Telegram пришли одновременно, хотя по расписанию должны были быть размазаны по дню). Расследование показало, что `due_time` в `TaskConfig` — это не время отправки, а дедлайн-метка, показываемая в тексте сообщения; фактическое планирование устроено правильно (индивидуальные one-shot APScheduler-джобы на каждый `TaskConfig`/`TaskInstance`, без общего периодического опроса). Наблюдавшийся симптом «всё разом» объясняется отдельным, уже понятным механизмом восстановления джобов при рестарте бота (`scheduler_recovery_service.py`) — это открытая, но не обязательная к фиксу часть этого дизайна (пользователь переключился на другую, более приоритетную задачу).

**Реальная задача этого дизайна** — шесть независимых, но связанных улучшений интеграции с Google Sheets и уведомлений:

1. Когда админ меняет расписание задачи (время, тип, значение расписания, активность) прямо в Google-таблице, а не через кнопки бота, изменение подтягивается в БД синком (`GoogleSheetsService.sync_tasks`), но уже зарегistrированный APScheduler-джоб не переустанавливается — задача продолжает выполняться по старому расписанию, пока не наступит следующий естественный пересчёт `next_run_at`.
2. В Google-таблице сейчас нет никакой видимости по факту отправки: ни времени, когда сообщение реально ушло в Telegram, ни дедлайна конкретного запуска (только шаблон задачи, без дат конкретных запусков).
3. Сама Google-таблица сейчас хранит время отправки и дедлайн в ОДНОЙ ячейке `due_time` — время отправки (`TaskConfig.time`) вычисляется из неё же. В коде бота (ручное редактирование через кнопки, `bot/handlers/admin/schedules.py`) эти два поля уже независимы, но табличный синк искусственно склеивает их. Плюс дедлайн сейчас всегда «сегодня до HH:MM» — нет способа задать дедлайн на другой день после отправки.
4. Owner не получает уведомление, когда сотрудник меняет статус задачи (взял в работу, выполнил, проблема, просрочено).
5. В Google-таблице нет истории смен статуса задач (взята/выполнена/не взята) — только сама таблица заданий.
6. Уже существующий механизм эскалации «задача не взята/не выполнена вовремя» (`reminders.overdue_after_hours`/`escalation_enabled`) не работает так, как задокументировано настройками — время эскалации жёстко закодировано, настройка часов игнорируется.

## Часть А — пересинхронизация джобов после Sheets-синка

**Что делаем:** `sync_tasks()` начинает возвращать (через `SyncReport`) список `config_id`, которые были добавлены, обновлены или деактивированы за проход синка. После того как транзакция `sync_all()` реально закоммитится — и при ручном «✅ Применить» в админке, и в фоновом `auto_sync_job` — для каждого такого `config_id` вызывается уже существующий `SchedulerService.rebuild_config_job(config_id)`.

`rebuild_config_job` уже идемпотентен и уже правильно обрабатывает оба случая: пересчитывает `next_run_at` по свежим данным конфига и переустанавливает APScheduler-джоб, либо (если конфиг стал неактивным) снимает джоб вовсе. Никакой новой логики планирования писать не нужно — только подключить существующий вызов к пути синка из Sheets, аналогично тому, как он уже подключён к ручному редактированию расписания через кнопки бота (`bot/handlers/admin/schedules.py`).

**Область действия:** только конфиги, где реально изменились расписание-влияющие поля (`time`, `schedule_type`, `schedule_value`, `schedule_interval`, `run_on_weekends`, `is_active`) — но поскольку `rebuild_config_job` дёшев и идемпотентен, вызывать его для КАЖДОГО добавленного/обновлённого/деактивированного конфига (без diff по конкретным полям) — осознанное упрощение, а не необходимость точного diff.

## Часть Б — Журнал отправок в Google Sheets

**Цель:** после фактической отправки задачи в Telegram — асинхронно, отдельным фоновым заданием — дописывать строку в новый лист Google-таблицы «Журнал отправок»: задача, чат/тема, время отправки, дедлайн (дата+время).

**Почему отдельный лист, а не колонки в существующем листе задач:** в существующем листе одна строка = один шаблон задачи («каждый день в 09:00»), а не один конкретный запуск. У конкретного запуска есть конкретные дата+время отправки и конкретный дедлайн (с датой), которых у шаблона попросту нет. Записывать их в ту же строку шаблона означало бы либо терять историю (перезаписывать при каждом новом запуске), либо ломать модель «одна строка = один шаблон». Новый лист-журнал — по одной строке на каждую фактически отправленную `TaskInstance`, без потери истории.

### Компоненты

- **Новое поле** `TaskInstance.sheet_logged_at: datetime | None` (Alembic-миграция) — метка «эта запись уже выгружена в Журнал отправок»; защищает от задваивания строк при повторных прогонах джоба.
- **`SheetsClient`**: область доступа (`SCOPES`) расширяется с `spreadsheets.readonly` до полного `spreadsheets` (нужен write). Новый метод `append_rows(sheet_name: str, rows: list[list])`, использующий `gspread`. Если листа с указанным именем ещё нет в таблице — создаётся автоматически, с заголовком `["Задача", "Чат/тема", "Время отправки", "Дедлайн"]` первой строкой.
- **Новый фоновый джоб** `delivery_log_job(bot, session_factory)` (по образцу `auto_sync_job`):
  1. Выбирает `TaskInstance`, где `delivery_status == DeliveryStatus.SENT` и `sheet_logged_at IS NULL`.
  2. Для каждой строки: `title_snapshot` (колонка «Задача»); имя темы — `TopicRepository.get_by_id(topic_id).topic_name`, если `topic_id is not None`, иначе «—» (колонка «Чат/тема»); `message_sent_at` (колонка «Время отправки», формат `ДД.ММ.ГГГГ ЧЧ:ММ`); `due_at` (колонка «Дедлайн», тот же формат, с датой и временем — не только время суток, как в `TaskConfig.due_time`).
  3. Одним пакетным вызовом (`append_rows`) дописывает все найденные строки.
  4. Проставляет `sheet_logged_at = datetime.utcnow()` каждой обработанной `TaskInstance`, коммитит.
  5. Ничего не делает, если `delivery_log.enabled=False` (тот же паттерн, что у `auto_sync_job`/`sync.auto_enabled`).
- **Новые настройки** (обычные карточки в общем списке настроек `SETTINGS_REGISTRY`, без отдельного раздела админки — журнал не меняет бизнес-данные, дополнительная защита с dry-run/confirm избыточна):
  - `delivery_log.enabled` (bool, default `False`)
  - `delivery_log.interval_minutes` (int, default `60`, min `5`, max `1440`) — по аналогии с `sync.interval_minutes`.
  - Категория настроек — новая `"delivery_log"` (не `"sync"`): отдельная от чтения-из-Sheets забота, хоть и про ту же таблицу.
- **`SchedulerService.register_delivery_log_job()`** — по образцу `register_sync_job()`: снимает job `"delivery_log"`, если `delivery_log.enabled=True` — регистрирует `delivery_log_job` с интервалом `delivery_log.interval_minutes`.
- **`SCHEDULER_AFFECTING`** (`bot/handlers/admin/settings.py`) дополняется `"delivery_log.enabled"`, `"delivery_log.interval_minutes"`; в `apply_setting_input` — ветка `elif key.startswith("delivery_log."): await scheduler_svc.register_delivery_log_job()` (по аналогии с существующей веткой для `sync.*`).
- **Регистрация при старте бота** — `register_delivery_log_job()` вызывается там же, где сейчас `register_sync_job()` (`bot/main.py`).

### Обработка ошибок

Если Google Sheets API недоступен во время `delivery_log_job` (сеть, rate limit, протухшие credentials) — исключение ловится, логируется через существующий `logger`, ни одна `TaskInstance` не помечается `sheet_logged_at` — все необработанные строки просто попробуются снова на следующем интервале. Джоб полностью изолирован от пути отправки сообщений в Telegram: отправка уже случилась раньше и никак не зависит от успеха записи в журнал.

### Явно вне рамок (по решению пользователя в диалоге)

- Напоминания (`remind1`/`remind2`) и просрочка (`overdue`) в журнал не попадают — только первоначальная отправка задачи.
- Живой запрос к Google Sheets в момент самой отправки сообщения (минуя БД/автосинк) — не делается: контент и так читается свежим из БД при срабатывании джоба (`run_config` делает `repo.get_config(config_id)` заново при каждом срабатывании), а зависимость критического пути отправки от доступности Google API признана нежелательной.
- Отдельный раздел админки для журнала (с dry-run/preview, как у «Синхронизация») — не нужен, обычных карточек настроек достаточно.
- Исправление `scheduler_recovery_service.py` (эффект «всё отправилось разом» при рестарте бота) — отдельная, не решаемая этим дизайном задача.

## Часть В — независимые «время отправки» и «дедлайн (+N дней)» в Google Sheets

**Цель:** в самой Google-таблице (а не только в логе из Части Б) время отправки и дедлайн должны быть двумя независимо задаваемыми значениями, а дедлайн — уметь указывать на день ПОЗЖЕ дня отправки.

**Что уже есть и не трогается:** в БД `TaskConfig.time` (время отправки) и `TaskConfig.due_time` (время суток дедлайна) — уже два разных поля; ручное редактирование через кнопки бота (`bot/handlers/admin/schedules.py`) уже позволяет задавать их независимо. Менять эту часть не нужно.

**Что меняется:**

1. **Новое поле** `TaskConfig.due_days_offset: int` (default `0`) — сколько дней ПОСЛЕ дня отправки наступает дедлайн; `0` — тот же день (сохраняет текущее поведение для всех существующих задач). Alembic-миграция с `server_default="0"`.
2. **`bot/services/task_service.py:75-78`** (расчёт `due_at` при создании `TaskInstance`) меняется с
   ```python
   due_at = (datetime.combine(scheduled_at.date(), config.due_time)
             if config.due_time else scheduled_at + timedelta(hours=24))
   ```
   на
   ```python
   due_at = (datetime.combine(scheduled_at.date() + timedelta(days=config.due_days_offset),
                              config.due_time)
             if config.due_time else scheduled_at + timedelta(hours=24))
   ```
3. **Google-таблица (лист задач):** сейчас `sync_tasks()` вычисляет `TaskConfig.time` из ЯЧЕЙКИ `due_time` (`_moscow_to_utc(_parse_time(row.get("due_time")))`, `google_sheets_service.py:316`). Это меняется на чтение НОВОЙ, независимой колонки листа — `row.get("time")` (тот же формат ЧЧ:ММ по МСК, та же конвертация в UTC) — колонку `due_time` синк продолжает читать отдельно, как и раньше, только перестаёт также питать `time`. Плюс читается новая колонка `row.get("due_days_offset")` → `int(...)` или `0`, если ячейка пустая.
   - **Ручной шаг вне кода:** администратору нужно добавить в реальную Google-таблицу (лист задач) две новые колонки с заголовками `time` и `due_days_offset` — это разовое ручное действие с самой таблицей, не часть кода/плана.
4. **Админ-панель бота** (`bot/handlers/admin/schedules.py`): `due_days_offset` добавляется в `SCHEDULE_FIELDS`/`SCHEDULE_FIELD_LIST`/`FIELD_TITLES` (например, `"due_days_offset": "Срок: дней после отправки"`), парсинг — `validate_int(raw, 0, 30)` (по аналогии с `schedule_interval`, только `min_=0`, т.к. 0 — тот же день).

### Обратная совместимость

Существующие `TaskConfig` без явного `due_days_offset` в таблице получают `0` (миграция + `_int_or_none(...) or 0` при синке) — поведение «дедлайн сегодня» не меняется, пока админ явно не укажет смещение.

## Часть Г — уведомление owner о смене статуса задачи

**Контекст:** каждая смена статуса задачи уже безопасно проходит через `TaskRepository.transition_status` (единственный механизм смены статуса во всём проекте) и уже полностью логируется в таблицу `TaskLog` (`task_instance_id`, `user_id`, `action`, `old_status`, `new_status`, `comment`, `created_at`) — `bot/database/models.py:238-248`. `transition_status` вызывается из 8 разных мест кода (`bot/handlers/admin/operations.py`, `bot/services/approval_service.py` ×3, `bot/services/article_check_service.py` ×2, `bot/services/reminder_service.py`, `bot/services/task_service.py` ×2) — трогать все эти места не нужно.

**Что делаем:** новое фоновое задание (не мгновенно, с задержкой в несколько минут — по решению пользователя в диалоге), по аналогии с `delivery_log_job`/`auto_sync_job`:

1. **Новое поле** `TaskLog.owner_notified_at: datetime | None` (Alembic-миграция) — защита от повторных уведомлений по одной и той же записи.
2. **Новые настройки:** `status_notifications.enabled` (bool, default `False`), `status_notifications.targets` (тип как у `reminders.escalation_targets` — список ролей, default `["owner"]`), `status_notifications.interval_minutes` (int, default `5`, min `1`, max `60` — короче, чем у `sync`/`delivery_log`, т.к. уведомление о статусе не терпит часового опоздания). Категория настроек — новая `"status_notifications"`.
3. **Уведомляемые статусы — фиксированный список, не настройка** (YAGNI, по решению пользователя): `{IN_PROGRESS, COMPLETED, PROBLEM, OVERDUE}` — только эти 4 из 10 возможных.
4. **Новый джоб** `status_notification_job(bot, session_factory)`:
   - Ничего не делает, если `status_notifications.enabled=False`.
   - Выбирает `TaskLog`, где `new_status IN (IN_PROGRESS, COMPLETED, PROBLEM, OVERDUE)` и `owner_notified_at IS NULL`.
   - Для каждой записи: подтягивает `TaskInstance.title_snapshot` (по `task_instance_id`), при наличии `user_id` — имя пользователя, сменившего статус (иначе «система»/`action`, например `"auto:overdue"`), формирует текст вида `"📌 {title}: {old_status} → {new_status}"` (+ кто, если известно).
   - Отправляет каждому пользователю из ролей в `status_notifications.targets` (через `UserRepository.get_active_by_role`, тот же паттерн, что в `reminder_service.py:69,95`).
   - Проставляет `owner_notified_at = datetime.utcnow()`, коммитит.
   - Ошибки отправки в Telegram по одному получателю не должны прерывать обработку остальных (try/except вокруг `bot.send_message` на каждого адресата, как в `overdue_job`, `reminder_service.py`).
5. **`SchedulerService.register_status_notification_job()`** — по образцу `register_sync_job()`/`register_delivery_log_job()`.
6. **`SCHEDULER_AFFECTING`** дополняется `"status_notifications.enabled"`, `"status_notifications.interval_minutes"`; в `apply_setting_input` — ветка для `status_notifications.*` → `register_status_notification_job()`.
7. **Регистрация при старте** — `register_status_notification_job()` вызывается в `bot/main.py` рядом с остальными `register_*_job()`.

## Часть Д — новая вкладка «История статусов» в Google Sheets

**Цель:** отдельная вкладка (не «Журнал отправок» из Части Б — это разные листы с разным смыслом) с полной хронологией смен статуса каждой задачи: одна строка на каждую смену статуса (взята в работу/выполнена/не взята и т.д.) — прямое отражение таблицы `TaskLog`.

**Что делаем:** ещё одно фоновое задание, независимое от Части Г (разные получатели данных — Telegram vs Sheets, разный набор статусов — здесь ВСЕ переходы, не только 4 «уведомляемых»):

1. **Новое поле** `TaskLog.sheet_logged_at: datetime | None` (Alembic-миграция) — отдельный флаг от `owner_notified_at`, независимая идемпотентность.
2. **Новые настройки:** `status_history_log.enabled` (bool, default `False`), `status_history_log.interval_minutes` (int, default `60`, min `5`, max `1440` — как у `delivery_log`). Категория `"status_history_log"`.
3. **Новый лист** «История статусов» в той же Google-таблице — заголовок при автосоздании: `["Задача", "Был статус", "Стал статус", "Кто", "Когда"]`.
4. **Новый джоб** `status_history_job(bot, session_factory)`:
   - Ничего не делает, если `status_history_log.enabled=False`.
   - Выбирает ВСЕ `TaskLog`, где `sheet_logged_at IS NULL` (без фильтра по статусу — полная история, включая «взята в работу», «выполнена», и любые прочие переходы).
   - Строка: задача (`title_snapshot` инстанса), `old_status` (или «—», если `None` — первое создание), `new_status`, кто (имя пользователя по `user_id` или `action`, если `user_id is None` — например, автоматический переход `auto:overdue`), `created_at` записи `TaskLog` (формат `ДД.ММ.ГГГГ ЧЧ:ММ`).
   - Один пакетный `append_rows` на все найденные записи, затем проставляет `sheet_logged_at`, коммитит.
   - Та же обработка ошибок, что у `delivery_log_job` (Часть Б): сбой Google API — ничего не помечается, повтор на следующем интервале.
5. **`SchedulerService.register_status_history_job()`**, `SCHEDULER_AFFECTING`, `apply_setting_input`, регистрация в `bot/main.py` — по тому же образцу, что и в Части Б/Г.

## Часть Е — починка бага с фиксированными 24 часами до overdue

**Находка в диалоге:** в проекте уже есть механизм эскалации «задача не взята/не выполнена» — `reminders.overdue_enabled`, `reminders.overdue_after_hours` (default 24), `reminders.escalation_enabled` (default `False`), `reminders.escalation_targets` (default `["owner"]`), реализованный в `overdue_job` (`bot/services/reminder_service.py`, транзит `OPEN_STATUSES → OVERDUE` + уведомление). НО время срабатывания джоба сейчас жёстко закодировано:

```python
# bot/services/scheduler_service.py:141-144, register_instance_jobs
self.scheduler.add_job(
    overdue_job, "date", run_date=base + timedelta(hours=24),
    args=[inst.id, self.bot, self.session_factory],
    id=f"overdue:{inst.id}", replace_existing=True, misfire_grace_time=GRACE)
```

`reminders.overdue_after_hours` нигде не читается при регистрации этого джоба — значение настройки не влияет ни на что. **Фикс:** `register_instance_jobs` при регистрации `overdue:{inst.id}` читает `reminders.overdue_after_hours` (нужен доступ к настройкам — `register_instance_jobs` сейчас синхронный и без сессии/settings; потребуется сделать его асинхронным и открыть сессию через `self.session_factory`, как в `rebuild_config_job`, либо прокинуть уже загрученное значение настройки через вызывающий код, который создаёт `TaskInstance` — выбрать вариант при реализации, ориентируясь на то, как `register_instance_jobs` вызывается сейчас в `run_config`/`bot/handlers/admin/task_configs.py`) вместо жёстко закодированных `hours=24`.

**Что пользователю нужно сделать после фикса (не часть кода):** включить `reminders.escalation_enabled=True` и задать желаемое `reminders.overdue_after_hours` через админ-панель — тогда существующий (уже работающий, за исключением этого бага) механизм эскалации начнёт уведомлять owner/partner о задачах, которые не взяли в работу/не выполнили вовремя.

## Тестирование

- `SheetsClient.append_rows` — unit-тест с фейковым gspread-клиентом (как уже принято в проекте для `SheetsClient`/`read_rows`): создаёт лист при отсутствии, дописывает строки при наличии.
- `delivery_log_job` — интеграционный тест на реальной тестовой БД: создаёт `TaskInstance` в разных `delivery_status` (SENT/FAILED/PENDING), проверяет, что в журнал попадают только `SENT` и не более одного раза при повторном прогоне джоба (идемпотентность через `sheet_logged_at`).
- `sync_tasks()` — тест на то, что `SyncReport` содержит корректный список изменившихся `config_id` (добавленные + обновлённые + деактивированные); отдельный тест на то, что `time` теперь читается из своей колонки, а не из `due_time`.
- Интеграционный тест на `sync_all()`/`auto_sync_job`: после синка с изменённым временем задачи — `rebuild_config_job` вызван для затронутого `config_id`, APScheduler-джоб пересобран с новым `next_run_at`.
- `apply_setting_input`: изменение `delivery_log.enabled`/`delivery_log.interval_minutes` вызывает `register_delivery_log_job()` (по аналогии с существующим тестом для `sync.*`).
- `task_service.create_instance_for`: `due_days_offset=2` даёт `due_at` на 2 дня позже `scheduled_at.date()`; `due_days_offset=0` (или default) — прежнее поведение (тот же день).
- `schedules.py`: `due_days_offset` редактируется через кнопки бота, валидация отклоняет отрицательные и >30.
- `status_notification_job`: `TaskLog` с `new_status="in_progress"`/`"completed"`/`"problem"`/`"overdue"` порождает уведомление владельцам из `status_notifications.targets`, помечается `owner_notified_at`, повторный прогон не дублирует; `TaskLog` с прочими статусами (например `"waiting_approval"`) уведомление НЕ порождает.
- `status_history_job`: КАЖДАЯ запись `TaskLog` (без фильтра по статусу) попадает в лист «История статусов» ровно один раз; проверить отдельно запись с `user_id is None` (авто-переход, например overdue) — колонка «Кто» показывает `action`, а не падает на `None`.
- Регрессионный тест на фикс Части Е: создать `TaskInstance`, задать `reminders.overdue_after_hours` в нестандартное значение (например `2`), убедиться, что зарегистрированный job `overdue:{id}` имеет `run_date = scheduled_at + timedelta(hours=2)`, а не жёстко `+24`.

## Критерии готовности

1. Изменение времени/расписания задачи в Google-таблице (при включённом `sync.auto_enabled`) приводит к пересборке соответствующего APScheduler-джоба в течение одного интервала синка — без ручного вмешательства.
2. После включения `delivery_log.enabled` каждая успешно отправленная (`SENT`) задача в течение `delivery_log.interval_minutes` появляется новой строкой в листе «Журнал отправок» с корректными задачей, чатом/темой, временем отправки и дедлайном (дата+время).
3. Повторные прогоны `delivery_log_job` не создают дублирующихся строк.
4. Недоступность Google Sheets API не влияет на отправку сообщений в Telegram и не приводит к падению планировщика.
5. Время отправки (`time`) и дедлайн (`due_time`/`due_days_offset`) задаются в Google-таблице независимо друг от друга, дедлайн может быть на день позже дня отправки.
6. При включённом `status_notifications.enabled` owner получает Telegram-уведомление в течение `status_notifications.interval_minutes` после того, как задача перешла в `in_progress`/`completed`/`problem`/`overdue` — не чаще одного раза на переход.
7. При включённом `status_history_log.enabled` каждая смена статуса любой задачи появляется строкой в листе «История статусов» в течение `status_history_log.interval_minutes`.
8. После фикса Части Е изменение `reminders.overdue_after_hours` реально сдвигает момент перехода задачи в `OVERDUE` (проверяется через фактическое время регистрации `overdue:{id}` job'а).
6. Существующие задачи без `due_days_offset` в таблице продолжают работать как раньше (дедлайн в день отправки).
