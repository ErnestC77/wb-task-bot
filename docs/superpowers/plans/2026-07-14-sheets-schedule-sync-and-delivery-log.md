# Пересинхронизация расписания + Журнал отправок + независимые time/дедлайн — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** После синка из Google Sheets APScheduler-джобы пересобираются автоматически; каждая фактически отправленная задача дописывается строкой в новый лист «Журнал отправок»; время отправки и дедлайн (с offset в днях) задаются в таблице независимо.

**Architecture:** Три независимые части. A: `sync_tasks()` возвращает изменённые `config_id` через `SyncReport.changed_config_ids`, и оба вызывающих пути (админ-«Применить» и `auto_sync_job`) после коммита вызывают существующий идемпотентный `SchedulerService.rebuild_config_job()`. B: новая колонка `TaskInstance.sheet_logged_at` + write-scope у `SheetsClient` + периодический `delivery_log_job` (по образцу `auto_sync_job`) + пара настроек `delivery_log.*` + `register_delivery_log_job()`. C: новая колонка `TaskConfig.due_days_offset` + чтение `time` из собственной колонки листа + редактирование offset в админ-панели.

**Tech Stack:** Python 3.12, aiogram 3, SQLAlchemy 2 async, Alembic, APScheduler, gspread, pytest (asyncio_mode=auto; юнит-тесты на SQLite in-memory через фикстуры `session_factory`/`session` из `tests/conftest.py`, интеграционные — маркер `pg`, реальный PostgreSQL через `TEST_DATABASE_URL`).

**Repo root:** `C:\Users\mccaq\wb-task-bot` (ветка `feature/mvp-implementation`). Все пути ниже — от корня репозитория. Все команды выполняются из корня репозитория.

**Spec:** `docs/superpowers/specs/2026-07-14-sheets-schedule-sync-and-delivery-log-design.md` (утверждён).

## Global Constraints

- **Ничего физически не удаляется** из БД — только `is_active=False` (существующий Global Constraint проекта).
- **`sync_all()` — единая транзакция**; коммитит её только вызывающий код и только при успехе (см. docstring `sync_all`).
- **Коммит ДО пересборки job'а** — `rebuild_config_job` / `register_*_job` открывают СОБСТВЕННУЮ сессию через `session_factory` и на Postgres не увидят незакоммиченные данные (правило Task 27/28/33, комментарии в `apply_schedule_field` и `apply_setting_input`).
- **Планировщик работает в naive-UTC** (`AsyncIOScheduler(timezone="UTC")`, `datetime.utcnow()` везде); время в Google-таблице — московское (МСК = UTC+3), конвертация только через `_moscow_to_utc`.
- **Явный `remove_job` перед `add_job`** для интервальных/cron job'ов — `replace_existing=True` не работает до `scheduler.start()` (находка Task 12-13, комментарий в `register_report_job`).
- **Тексты интерфейса и docstrings — на русском**, формат даты-времени для людей — `%d.%m.%Y %H:%M`.
- **Никаких новых зависимостей** — только то, что уже в `requirements.txt`.
- **Линтера/тайпчекера в проекте нет** (`requirements-dev.txt`: только pytest/pytest-asyncio/aiosqlite) — верификация каждого шага только через pytest.
- **Цепочка Alembic-миграций:** текущий head — `0003`; эта работа добавляет `0004` (Task 4) и `0005` (Task 9), строго в этом порядке.
- Команда прогона тестов: `python -m pytest <файл> -q` (маркер `pg` по умолчанию исключён через `pytest.ini` `addopts = -m "not pg"`).
- Вне рамок (по утверждённой спеке): фикс `scheduler_recovery_service.py`; логирование remind1/remind2/overdue в журнал; отдельный раздел админки для журнала (обычных карточек настроек достаточно); живое чтение Sheets в момент отправки.

---

## Часть А — пересборка джобов после Sheets-синка

### Task 1: `SyncReport.changed_config_ids` из `sync_tasks()`

**Files:**
- Modify: `bot/services/google_sheets_service.py` (dataclass `SyncReport`, строки ~40-45; метод `sync_tasks`, строки ~274-336)
- Test: `tests/test_sheets_service.py`

**Interfaces:**
- Consumes: существующие `SyncReport`, `sync_tasks(rows, dry_run)`, `TaskRepository.upsert_config(data) -> TaskConfig` (делает flush — `cfg.id` заполнен), `TaskRepository.get_active_configs_not_in(seen)`.
- Produces: `SyncReport.changed_config_ids: list[int]` — id всех добавленных, обновлённых и деактивированных `TaskConfig` за проход `sync_tasks()`. Заполняется ТОЛЬКО `sync_tasks()` (другие листы планировщик не трогают). Tasks 2 и 3 читают это поле после `dry_run=False`-прогона.

- [ ] **Step 1: Написать падающий тест**

Добавить в конец `tests/test_sheets_service.py` (константа `TASK_ROWS` и импорты `select`, `TaskConfig`, `GoogleSheetsService` уже есть в файле):

```python
async def test_sync_tasks_reports_changed_config_ids(session_factory):
    """Часть А: SyncReport перечисляет id добавленных/обновлённых/
    деактивированных конфигов — по ним вызывающий код пересоберёт джобы."""
    from bot.services.google_sheets_service import SyncReport

    assert SyncReport().changed_config_ids == []          # default — пустой список

    async with session_factory() as s:                    # добавление
        svc = GoogleSheetsService(s, client=None)
        report = await svc.sync_tasks(TASK_ROWS, dry_run=False)
        await s.commit()
        cfg = await s.scalar(select(TaskConfig).where(
            TaskConfig.external_task_id == "articles_check_all"))
        assert report.changed_config_ids == [cfg.id]
        cfg_id = cfg.id

    async with session_factory() as s:                    # обновление
        svc = GoogleSheetsService(s, client=None)
        report = await svc.sync_tasks(TASK_ROWS, dry_run=False)
        await s.commit()
        assert report.changed_config_ids == [cfg_id]

    async with session_factory() as s:                    # деактивация
        svc = GoogleSheetsService(s, client=None)
        report = await svc.sync_tasks([], dry_run=False)
        await s.commit()
        assert report.changed_config_ids == [cfg_id]
```

- [ ] **Step 2: Убедиться, что тест падает**

Run: `python -m pytest tests/test_sheets_service.py::test_sync_tasks_reports_changed_config_ids -q`
Expected: FAIL — `AttributeError: 'SyncReport' object has no attribute 'changed_config_ids'`.

- [ ] **Step 3: Реализация**

В `bot/services/google_sheets_service.py`:

3a. Дополнить dataclass:

```python
@dataclass
class SyncReport:
    added: int = 0
    updated: int = 0
    deactivated: int = 0
    skipped_conflicts: list[str] = field(default_factory=list)
    # id добавленных/обновлённых/деактивированных TaskConfig за проход
    # sync_tasks() (другие листы планировщик не трогают — там поле пустое).
    # Использовать ТОЛЬКО после реального (dry_run=False) закоммиченного
    # прогона: при dry-run savepoint откатывается, id добавленных строк —
    # временные и в БД не существуют.
    changed_config_ids: list[int] = field(default_factory=list)
```

3b. В `sync_tasks()` заменить строку `await task_repo.upsert_config(data)` на:

```python
                    cfg = await task_repo.upsert_config(data)
                    report.changed_config_ids.append(cfg.id)
```

3c. В цикле деактивации того же метода заменить:

```python
                for stale in await task_repo.get_active_configs_not_in(seen):
                    stale.is_active = False
                    report.deactivated += 1
```

на:

```python
                for stale in await task_repo.get_active_configs_not_in(seen):
                    stale.is_active = False
                    report.deactivated += 1
                    report.changed_config_ids.append(stale.id)
```

Конфликтные записи (`skipped_conflicts`) в `changed_config_ids` НЕ попадают — там стоит `continue` до upsert, конфиг не менялся, пересобирать нечего.

- [ ] **Step 4: Убедиться, что тест проходит**

Run: `python -m pytest tests/test_sheets_service.py -q`
Expected: PASS все тесты файла (`vars(v)` в audit-json `sync_all` сериализует новый список без проблем).

- [ ] **Step 5: Полный юнит-прогон и коммит**

Run: `python -m pytest -q` — Expected: PASS (pg-тесты skipped по умолчанию).

```bash
git add bot/services/google_sheets_service.py tests/test_sheets_service.py
git commit -m "feat: SyncReport.changed_config_ids lists task configs touched by sync_tasks"
```

---

### Task 2: пересборка джобов после «✅ Применить» в админке

**Files:**
- Modify: `bot/handlers/admin/sync.py` (функция `apply_sync` ~строка 93, функция `_start_apply` ~строка 185, ветка `"apply"` в `handle_syn_callback` ~строка 344)
- Test: `tests/test_admin_sync.py`

**Interfaces:**
- Consumes: `SyncReport.changed_config_ids` (Task 1); существующие `SchedulerService.rebuild_config_job(config_id)` (идемпотентен, открывает свою сессию), `_extract_scheduler_svc(dispatcher)` (уже есть в `sync.py`), механизм `AdminService.confirm_token`/`execute_confirmed` (`op(session)` получает сессию подтверждающего запроса параметром, коммит — в `handle_confirm` после `execute_confirmed`).
- Produces: `apply_sync(session, actor, client, scheduler_svc=None) -> dict[str, SyncReport]` — коммитит ВНУТРИ себя (до пересборки), затем пересобирает джобы всех изменённых конфигов. Повторный `session.commit()` вызывающего кода — безопасный no-op.

- [ ] **Step 1: Написать падающий тест**

Добавить в конец `tests/test_admin_sync.py` (в файле уже импортированы `AsyncMock, MagicMock, patch`, `select`, `Role`, `UserRepository`; хелпер `_reply_markup` есть):

```python
async def test_confirmed_apply_rebuilds_jobs_for_changed_configs(session_factory):
    """Часть А: подтверждённое «✅ Применить» после коммита пересобирает
    APScheduler-джоб каждого добавленного/обновлённого конфига."""
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from bot.database.models import TaskConfig
    from bot.handlers.admin.sync import handle_syn_callback
    from bot.keyboards.admin.confirm import ConfirmCb
    from bot.keyboards.admin.sync import SynCb
    from bot.services.admin_service import AdminService
    from bot.services.scheduler_service import SchedulerService

    async with session_factory() as s:
        owner = await UserRepository(s).upsert(telegram_id=1, name="O", role=Role.OWNER)
        await s.commit()
        owner_tg = owner.telegram_id

    scheduler = AsyncIOScheduler()
    scheduler.wb_service = SchedulerService(scheduler, AsyncMock(), session_factory)

    class FakeDispatcher:
        workflow_data = {"scheduler": scheduler}

    task_rows = [{"external_task_id": "resync_me", "title": "Задача", "scenario": "simple",
                  "schedule_type": "daily", "time": "18:00", "due_time": "18:00",
                  "active": "1"}]
    fake_client = MagicMock()
    fake_client.read_rows = lambda sheet_name: (
        task_rows if sheet_name == "Tasks_Config" else [])

    async with session_factory() as s:
        callback = AsyncMock()
        callback.from_user.id = owner_tg
        await handle_syn_callback(callback, SynCb(a="apply"), s,
                                  dispatcher=FakeDispatcher())
        kb = _reply_markup(callback)
        confirm_cb = ConfirmCb.unpack(kb.inline_keyboard[0][0].callback_data)
        svc = AdminService(s)
        with patch("bot.handlers.admin.sync.SheetsClient", return_value=fake_client):
            ok = await svc.execute_confirmed(confirm_cb.t, s)
        await s.commit()                       # no-op: apply_sync уже закоммитила
        assert ok is True

    async with session_factory() as s:
        cfg = await s.scalar(select(TaskConfig).where(
            TaskConfig.external_task_id == "resync_me"))
        assert cfg is not None
        assert cfg.next_run_at is not None      # rebuild_config_job пересчитал
        cfg_id = cfg.id
    assert scheduler.get_job(f"config:{cfg_id}") is not None
```

- [ ] **Step 2: Убедиться, что тест падает**

Run: `python -m pytest tests/test_admin_sync.py::test_confirmed_apply_rebuilds_jobs_for_changed_configs -q`
Expected: FAIL — `assert cfg.next_run_at is not None` (текущий `apply_sync` джобы не пересобирает).

- [ ] **Step 3: Реализация**

В `bot/handlers/admin/sync.py`:

3a. Заменить `apply_sync` целиком:

```python
async def apply_sync(session, actor, client, scheduler_svc=None) -> dict:
    """Часть А: после реального синка пересобирает APScheduler-джобы всех
    изменённых (добавленных/обновлённых/деактивированных) TaskConfig — иначе
    правка расписания прямо в Google-таблице продолжала бы исполняться по
    старому расписанию до следующего естественного пересчёта next_run_at.

    Коммит ВНУТРИ, ДО пересборки: rebuild_config_job открывает СВОЮ сессию
    через session_factory и на Postgres не увидит незакоммиченные данные
    (правило Task 27/28/33 — тот же паттерн, что apply_schedule_field и
    toggle_auto_sync). Повторный commit() вызывающего кода — безопасный no-op."""
    svc = GoogleSheetsService(session, client=client)
    results = await svc.sync_all(dry_run=False, actor_user_id=actor.id)
    await session.commit()
    if scheduler_svc is not None:
        for config_id in results["tasks"].changed_config_ids:
            await scheduler_svc.rebuild_config_job(config_id)
    return results
```

3b. Заменить `_start_apply` целиком (новый параметр `scheduler_svc`, захватываемый op-замыканием; остальное как было):

```python
async def _start_apply(callback: CallbackQuery, session, actor, svc: AdminService,
                       scheduler_svc=None) -> None:
    """Реальная синхронизация — опасная операция (реально меняет данные в
    БД), только через confirm_token (см. docstring модуля). `bot`/строковые
    значения захватываются замыканием на момент создания токена, а не
    `session` (Task 27 fix). `scheduler_svc` — долгоживущий сервис, не
    привязанный к сессии, замыканием захватывается безопасно."""
    settings_svc = SettingService(session)
    spreadsheet_id = (str(await settings_svc.get("sync.spreadsheet_id"))
                      or get_settings().google_sheets_spreadsheet_id)
    creds_file = get_settings().google_sheets_credentials_file

    async def op(session) -> None:
        client = SheetsClient(creds_file, spreadsheet_id)
        await apply_sync(session, actor, client, scheduler_svc)

    token = svc.confirm_token(
        "sync.apply", op, required_permission=PERMISSION, creator_actor_id=actor.id)
    masked = mask_spreadsheet_id(spreadsheet_id) if spreadsheet_id else "(не задано)"
    await callback.message.edit_text(
        "Применить синхронизацию с Google Sheets прямо сейчас?\n"
        f"Spreadsheet ID: {code(masked)}\n"
        "Это реально изменит данные в БД (пользователи/темы/задачи/артикулы).",
        reply_markup=confirm_keyboard(token))
    await callback.answer()
```

3c. В `handle_syn_callback` заменить ветку:

```python
    elif action == "apply":
        await _start_apply(callback, session, actor, svc)
```

на:

```python
    elif action == "apply":
        await _start_apply(callback, session, actor, svc,
                           _extract_scheduler_svc(dispatcher))
```

- [ ] **Step 4: Убедиться, что тесты проходят**

Run: `python -m pytest tests/test_admin_sync.py -q`
Expected: PASS, включая старый `test_apply_goes_through_confirm_token_with_sync_run` (там dispatcher не передан → `scheduler_svc=None` → пересборки нет, прежнее поведение).

- [ ] **Step 5: Полный юнит-прогон и коммит**

Run: `python -m pytest -q` — Expected: PASS.

```bash
git add bot/handlers/admin/sync.py tests/test_admin_sync.py
git commit -m "feat: admin apply-sync rebuilds scheduler jobs for changed task configs"
```

---

### Task 3: пересборка джобов в фоновом `auto_sync_job`

**Files:**
- Modify: `bot/services/google_sheets_service.py` (функция `auto_sync_job`, строки ~369-384)
- Modify: `bot/services/scheduler_service.py` (метод `register_sync_job`, строки ~168-180)
- Test: `tests/test_sheets_service.py`

**Interfaces:**
- Consumes: `SyncReport.changed_config_ids` (Task 1), `SchedulerService.rebuild_config_job(config_id)`.
- Produces: `auto_sync_job(bot, session_factory, scheduler_svc=None)` — третий опциональный параметр; `register_sync_job()` регистрирует job с `args=[self.bot, self.session_factory, self]`, чтобы job мог вызвать `rebuild_config_job` обратно.

- [ ] **Step 1: Написать падающие тесты**

1a. В `tests/test_sheets_service.py` заменить строку импорта `from unittest.mock import AsyncMock` на:

```python
from unittest.mock import AsyncMock, MagicMock, patch
```

1b. Добавить в конец файла:

```python
async def test_auto_sync_job_rebuilds_jobs_for_changed_configs(session_factory):
    """Часть А: фоновый auto_sync_job после коммита пересобирает джобы
    изменённых конфигов через переданный SchedulerService."""
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from bot.services.scheduler_service import SchedulerService

    async with session_factory() as s:
        await SettingService(s).set("sync.auto_enabled", True, actor_user_id=None)
        await s.commit()

    scheduler = AsyncIOScheduler()
    svc = SchedulerService(scheduler, AsyncMock(), session_factory)
    task_rows = [{"external_task_id": "auto_resync_me", "title": "Задача",
                  "scenario": "simple", "schedule_type": "daily",
                  "time": "18:00", "due_time": "18:00", "active": "1"}]
    fake_client = MagicMock()
    fake_client.read_rows = lambda sheet_name: (
        task_rows if sheet_name == "Tasks_Config" else [])
    fake_settings = MagicMock(google_sheets_credentials_file="creds.json",
                              google_sheets_spreadsheet_id="sheet-id")

    with patch("bot.services.google_sheets_service.SheetsClient",
               return_value=fake_client), \
         patch("bot.services.google_sheets_service.get_settings",
               return_value=fake_settings):
        await auto_sync_job(AsyncMock(), session_factory, scheduler_svc=svc)

    async with session_factory() as s:
        cfg = await s.scalar(select(TaskConfig).where(
            TaskConfig.external_task_id == "auto_resync_me"))
        assert cfg is not None
        assert cfg.next_run_at is not None
        cfg_id = cfg.id
    assert scheduler.get_job(f"config:{cfg_id}") is not None


async def test_register_sync_job_passes_scheduler_service_to_job(session_factory):
    """register_sync_job обязан отдавать job'у сам SchedulerService третьим
    аргументом — иначе auto_sync_job не сможет пересобрать джобы конфигов."""
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from bot.services.scheduler_service import SchedulerService

    async with session_factory() as s:
        await SettingService(s).set("sync.auto_enabled", True, actor_user_id=None)
        await s.commit()

    scheduler = AsyncIOScheduler()
    svc = SchedulerService(scheduler, AsyncMock(), session_factory)
    await svc.register_sync_job()
    job = scheduler.get_job("auto_sync")
    assert job is not None
    assert len(job.args) == 3
    assert job.args[2] is svc
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `python -m pytest tests/test_sheets_service.py::test_auto_sync_job_rebuilds_jobs_for_changed_configs tests/test_sheets_service.py::test_register_sync_job_passes_scheduler_service_to_job -q`
Expected: FAIL — `TypeError: auto_sync_job() got an unexpected keyword argument 'scheduler_svc'` и `assert len(job.args) == 3` (сейчас 2).

- [ ] **Step 3: Реализация**

3a. В `bot/services/google_sheets_service.py` заменить `auto_sync_job` целиком:

```python
async def auto_sync_job(bot, session_factory, scheduler_svc=None) -> None:
    """Job-обработчик автосинхронизации (регистрируется
    SchedulerService.register_sync_job — Task 12). Ничего не делает, если
    sync.auto_enabled=False.

    Часть А: после коммита пересобирает APScheduler-джобы изменённых
    конфигов через scheduler_svc (сам SchedulerService, передан
    register_sync_job'ом третьим аргументом). Пересборка — ПОСЛЕ выхода из
    сессии: rebuild_config_job открывает собственную сессию и должен видеть
    уже закоммиченные данные (правило Task 27/28/33)."""
    from bot.database.db import async_session_factory

    factory = session_factory or async_session_factory
    async with factory() as session:
        settings = SettingService(session)
        if not bool(await settings.get("sync.auto_enabled")):
            return
        spreadsheet_id = (str(await settings.get("sync.spreadsheet_id"))
                          or get_settings().google_sheets_spreadsheet_id)
        client = SheetsClient(get_settings().google_sheets_credentials_file, spreadsheet_id)
        results = await GoogleSheetsService(session, client).sync_all(
            dry_run=False, actor_user_id=None)
        await session.commit()
    if scheduler_svc is not None:
        for config_id in results["tasks"].changed_config_ids:
            await scheduler_svc.rebuild_config_job(config_id)
```

3b. В `bot/services/scheduler_service.py`, в `register_sync_job`, заменить:

```python
        if enabled:
            self.scheduler.add_job(auto_sync_job, "interval", minutes=minutes,
                                   args=[self.bot, self.session_factory],
                                   id="auto_sync", misfire_grace_time=GRACE)
```

на:

```python
        if enabled:
            # self третьим аргументом: auto_sync_job после коммита вызывает
            # rebuild_config_job для изменённых синком конфигов (Часть А).
            self.scheduler.add_job(auto_sync_job, "interval", minutes=minutes,
                                   args=[self.bot, self.session_factory, self],
                                   id="auto_sync", misfire_grace_time=GRACE)
```

- [ ] **Step 4: Убедиться, что тесты проходят**

Run: `python -m pytest tests/test_sheets_service.py tests/test_scheduler_service.py tests/test_admin_sync.py -q`
Expected: PASS (старые вызовы `auto_sync_job(bot, session_factory)` работают — параметр опциональный).

- [ ] **Step 5: Полный юнит-прогон и коммит**

Run: `python -m pytest -q` — Expected: PASS.

```bash
git add bot/services/google_sheets_service.py bot/services/scheduler_service.py tests/test_sheets_service.py
git commit -m "feat: auto_sync_job rebuilds scheduler jobs for configs changed by sheet sync"
```

---

## Часть Б — Журнал отправок

### Task 4: колонка `TaskInstance.sheet_logged_at` + выборка неотгруженных

**Files:**
- Create: `bot/database/migrations/versions/0004_sheet_logged_at.py`
- Modify: `bot/database/models.py` (класс `TaskInstance`, блок «доставка (10.4)», после `message_sent_at`, ~строка 217)
- Modify: `bot/database/repositories/task_repository.py`
- Test: `tests/test_task_repository.py`

**Interfaces:**
- Consumes: существующие `DeliveryStatus.SENT`, `TaskInstance.message_sent_at`, фабрика `make_config` из `tests/test_task_service.py`.
- Produces: `TaskInstance.sheet_logged_at: datetime | None` (nullable, без default — в стиле `message_sent_at`); `TaskRepository.get_sent_unlogged() -> list[TaskInstance]` — SENT-инстансы с `sheet_logged_at IS NULL`, отсортированные по `message_sent_at`. Task 7 использует оба.

- [ ] **Step 1: Написать падающий тест**

Добавить в конец `tests/test_task_repository.py`:

```python
async def test_get_sent_unlogged_filters_status_and_flag(session):
    """Часть Б: для «Журнала отправок» выбираются только SENT-инстансы, ещё
    не выгруженные (sheet_logged_at IS NULL)."""
    from datetime import datetime
    from bot.database.models import DeliveryStatus
    from bot.database.repositories.task_repository import TaskRepository
    from bot.services.task_service import TaskService
    from tests.test_task_service import make_config

    cfg, _ = await make_config(session)
    svc = TaskService(session)
    sent = await svc.create_instance_for(cfg, datetime(2026, 7, 10, 9, 0))
    pending = await svc.create_instance_for(cfg, datetime(2026, 7, 11, 9, 0))
    logged = await svc.create_instance_for(cfg, datetime(2026, 7, 12, 9, 0))
    sent.delivery_status = DeliveryStatus.SENT
    sent.message_sent_at = datetime(2026, 7, 10, 9, 1)
    logged.delivery_status = DeliveryStatus.SENT
    logged.message_sent_at = datetime(2026, 7, 12, 9, 1)
    logged.sheet_logged_at = datetime(2026, 7, 12, 10, 0)   # уже выгружен
    await session.commit()

    rows = await TaskRepository(session).get_sent_unlogged()
    assert [r.id for r in rows] == [sent.id]                # pending и logged — мимо
```

- [ ] **Step 2: Убедиться, что тест падает**

Run: `python -m pytest tests/test_task_repository.py::test_get_sent_unlogged_filters_status_and_flag -q`
Expected: FAIL — у `TaskInstance` нет атрибута `sheet_logged_at`.

- [ ] **Step 3: Реализация**

3a. В `bot/database/models.py`, в классе `TaskInstance`, после строки `message_sent_at: Mapped[datetime | None] = mapped_column(DateTime)` добавить:

```python
    # выгружено в лист «Журнал отправок» (Часть Б) — защита от задваивания строк
    sheet_logged_at: Mapped[datetime | None] = mapped_column(DateTime)
```

3b. В `bot/database/repositories/task_repository.py` добавить метод после `get_pending_delivery`:

```python
    async def get_sent_unlogged(self) -> list[TaskInstance]:
        """Часть Б: отправленные в Telegram, но ещё не выгруженные в лист
        «Журнал отправок» (sheet_logged_at IS NULL)."""
        return list(await self.session.scalars(
            select(TaskInstance).where(
                TaskInstance.delivery_status == DeliveryStatus.SENT,
                TaskInstance.sheet_logged_at.is_(None))
            .order_by(TaskInstance.message_sent_at)))
```

3c. Создать `bot/database/migrations/versions/0004_sheet_logged_at.py`:

```python
"""add sheet_logged_at to task_instances

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-14 12:00:00.000000

"""
import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"


def upgrade() -> None:
    op.add_column(
        "task_instances",
        sa.Column("sheet_logged_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("task_instances", "sheet_logged_at")
```

- [ ] **Step 4: Убедиться, что тесты проходят**

Run: `python -m pytest tests/test_task_repository.py tests/test_models.py -q`
Expected: PASS.

- [ ] **Step 5: Проверка миграции (если доступен тестовый Postgres)**

Run: `python -m pytest tests/integration/test_alembic_migration.py -m pg -q`
Expected: PASS при заданном `TEST_DATABASE_URL`; иначе — «skipped» (module-level skip в `tests/integration/conftest.py`, это нормально).

- [ ] **Step 6: Полный юнит-прогон и коммит**

Run: `python -m pytest -q` — Expected: PASS.

```bash
git add bot/database/models.py bot/database/repositories/task_repository.py bot/database/migrations/versions/0004_sheet_logged_at.py tests/test_task_repository.py
git commit -m "feat: TaskInstance.sheet_logged_at column and sent-unlogged query for delivery log"
```

---

### Task 5: write-scope и `SheetsClient.append_rows`

**Files:**
- Modify: `bot/services/google_sheets_service.py` (константа `SCOPES` ~строка 37, класс `SheetsClient` ~строки 48-57)
- Test: `tests/test_sheets_service.py`

**Interfaces:**
- Consumes: gspread (`self._spreadsheet` — объект gspread Spreadsheet: `worksheet()`, `add_worksheet()`; worksheet: `append_row()`, `append_rows()`; исключение `gspread.exceptions.WorksheetNotFound`).
- Produces: `SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]` (read-write); модульная константа `DELIVERY_LOG_HEADER = ["Задача", "Чат/тема", "Время отправки", "Дедлайн"]`; `SheetsClient.append_rows(sheet_name: str, rows: list[list]) -> None` — get-or-create листа (при создании первой строкой пишется заголовок), затем пакетное дописывание. Task 7 вызывает `append_rows`.

- [ ] **Step 1: Написать падающие тесты**

Добавить в конец `tests/test_sheets_service.py` (`MagicMock` уже импортирован после Task 3; если Task 3 ещё не выполнена — заменить строку `from unittest.mock import AsyncMock` на `from unittest.mock import AsyncMock, MagicMock, patch`):

```python
def _client_with_fake_spreadsheet(fake) -> SheetsClient:
    """SheetsClient без __init__ (реальный конструктор ходит в Google API);
    тесты подставляют фейковый gspread-Spreadsheet напрямую — тот же принцип
    «тонкая обёртка подменяется фейком», что и для read_rows."""
    client = SheetsClient.__new__(SheetsClient)
    client._spreadsheet = fake
    return client


def test_scopes_allow_write():
    from bot.services.google_sheets_service import SCOPES
    assert SCOPES == ["https://www.googleapis.com/auth/spreadsheets"]


def test_append_rows_appends_to_existing_sheet():
    ws = MagicMock()
    fake = MagicMock()
    fake.worksheet.return_value = ws
    client = _client_with_fake_spreadsheet(fake)
    client.append_rows("Журнал отправок", [["Задача 1", "Товары",
                                            "10.07.2026 09:01", "10.07.2026 12:00"]])
    ws.append_rows.assert_called_once_with(
        [["Задача 1", "Товары", "10.07.2026 09:01", "10.07.2026 12:00"]])
    fake.add_worksheet.assert_not_called()


def test_append_rows_creates_missing_sheet_with_header():
    import gspread
    ws = MagicMock()
    fake = MagicMock()
    fake.worksheet.side_effect = gspread.exceptions.WorksheetNotFound("нет листа")
    fake.add_worksheet.return_value = ws
    client = _client_with_fake_spreadsheet(fake)
    client.append_rows("Журнал отправок", [["a", "b", "c", "d"]])
    ws.append_row.assert_called_once_with(
        ["Задача", "Чат/тема", "Время отправки", "Дедлайн"])
    ws.append_rows.assert_called_once_with([["a", "b", "c", "d"]])
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `python -m pytest tests/test_sheets_service.py::test_scopes_allow_write tests/test_sheets_service.py::test_append_rows_appends_to_existing_sheet tests/test_sheets_service.py::test_append_rows_creates_missing_sheet_with_header -q`
Expected: FAIL — `SCOPES` содержит `.readonly`; у `SheetsClient` нет `append_rows`.

- [ ] **Step 3: Реализация**

В `bot/services/google_sheets_service.py`:

3a. Заменить:

```python
SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
```

на:

```python
# Часть Б: полный scope вместо .readonly — «Журнал отправок» пишет в таблицу.
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
DELIVERY_LOG_HEADER = ["Задача", "Чат/тема", "Время отправки", "Дедлайн"]
```

3b. В классе `SheetsClient` после метода `read_rows` добавить:

```python
    def append_rows(self, sheet_name: str, rows: list[list]) -> None:
        """Дописывает строки в лист; если листа нет — создаёт его и пишет
        первой строкой заголовок DELIVERY_LOG_HEADER (Часть Б)."""
        try:
            ws = self._spreadsheet.worksheet(sheet_name)
        except gspread.exceptions.WorksheetNotFound:
            ws = self._spreadsheet.add_worksheet(
                title=sheet_name, rows=1, cols=len(DELIVERY_LOG_HEADER))
            ws.append_row(DELIVERY_LOG_HEADER)
        ws.append_rows(rows)
```

- [ ] **Step 4: Убедиться, что тесты проходят**

Run: `python -m pytest tests/test_sheets_service.py -q`
Expected: PASS.

- [ ] **Step 5: Полный юнит-прогон и коммит**

Run: `python -m pytest -q` — Expected: PASS.

```bash
git add bot/services/google_sheets_service.py tests/test_sheets_service.py
git commit -m "feat: SheetsClient write scope and append_rows with get-or-create sheet"
```

---

### Task 6: настройки `delivery_log.*` в реестре

**Files:**
- Modify: `bot/services/setting_service.py` (функция `_defs()`, после блока `# --- sync ---`, ~строка 136)
- Modify: `bot/keyboards/admin/settings.py` (словарь `CATEGORY_TITLES`, строки 7-15)
- Test: `tests/test_setting_service.py`

**Interfaces:**
- Consumes: существующие `SettingDef`, `SETTINGS_REGISTRY`, `SettingService.get`.
- Produces: ключи `delivery_log.enabled` (bool, default `False`) и `delivery_log.interval_minutes` (int, default `60`, min 5, max 1440), новая категория `"delivery_log"` (НЕ `"sync"`); заголовок категории в `CATEGORY_TITLES`. Tasks 7 и 8 читают эти ключи. Категория автоматически появляется в разделе «⚙ Настройки» (`ALL_CATEGORIES` строится из реестра) — отдельный раздел админки по спеке не нужен.

- [ ] **Step 1: Написать падающие тесты**

Добавить в конец `tests/test_setting_service.py`:

```python
async def test_delivery_log_settings_registered_with_defaults(session):
    """Часть Б: настройки журнала отправок — обычные карточки в общем
    реестре, категория delivery_log (не sync)."""
    from bot.services.setting_service import SETTINGS_REGISTRY, SettingService

    svc = SettingService(session)
    assert await svc.get("delivery_log.enabled") is False
    assert await svc.get("delivery_log.interval_minutes") == 60
    d = SETTINGS_REGISTRY["delivery_log.interval_minutes"]
    assert (d.min_, d.max_, d.category) == (5, 1440, "delivery_log")
    assert SETTINGS_REGISTRY["delivery_log.enabled"].category == "delivery_log"


def test_delivery_log_category_has_title():
    from bot.keyboards.admin.settings import CATEGORY_TITLES
    assert CATEGORY_TITLES["delivery_log"] == "Журнал отправок"
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `python -m pytest tests/test_setting_service.py -q`
Expected: FAIL — `KeyError: 'Неизвестная настройка: delivery_log.enabled'` и `KeyError: 'delivery_log'`.

- [ ] **Step 3: Реализация**

3a. В `bot/services/setting_service.py`, в `_defs()`, после строки `SettingDef("sync.dry_run_default", bool, True, "sync"),` добавить:

```python
        # --- delivery_log ---
        SettingDef("delivery_log.enabled", bool, False, "delivery_log",
                   "Выгружать отправленные задачи в лист «Журнал отправок»"),
        SettingDef("delivery_log.interval_minutes", int, 60, "delivery_log",
                   "Интервал выгрузки журнала (минуты)", min_=5, max_=1440),
```

3b. В `bot/keyboards/admin/settings.py`, в `CATEGORY_TITLES`, после `"sync": "Синхронизация Google Sheets",` добавить:

```python
    "delivery_log": "Журнал отправок",
```

- [ ] **Step 4: Убедиться, что тесты проходят**

Run: `python -m pytest tests/test_setting_service.py tests/test_admin_settings.py -q`
Expected: PASS.

- [ ] **Step 5: Полный юнит-прогон и коммит**

Run: `python -m pytest -q` — Expected: PASS.

```bash
git add bot/services/setting_service.py bot/keyboards/admin/settings.py tests/test_setting_service.py
git commit -m "feat: delivery_log.enabled and delivery_log.interval_minutes settings"
```

---

### Task 7: фоновый `delivery_log_job`

**Files:**
- Modify: `bot/services/google_sheets_service.py` (новая функция после `auto_sync_job`)
- Test: `tests/test_sheets_service.py`
- Create: `tests/integration/test_delivery_log_pg.py`

**Interfaces:**
- Consumes: `TaskRepository.get_sent_unlogged()` (Task 4), `SheetsClient.append_rows(sheet_name, rows)` (Task 5), настройки `delivery_log.enabled` / `sync.spreadsheet_id` (Task 6), `TopicRepository.get_by_id(topic_id) -> Topic | None`, `get_settings().google_sheets_credentials_file` / `.google_sheets_spreadsheet_id`, модульный `logger`.
- Produces: `delivery_log_job(bot, session_factory) -> None` и константа `DELIVERY_LOG_SHEET_NAME = "Журнал отправок"`. Task 8 регистрирует job по id `"delivery_log"`.

- [ ] **Step 1: Написать падающие тесты**

1a. В `tests/test_sheets_service.py` убедиться, что первые две строки импортов такие (дополнить, если нет):

```python
from datetime import datetime, time, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
```

и что из сервиса импортируется job (заменить существующий импорт сервиса):

```python
from bot.services.google_sheets_service import (
    GoogleSheetsService, SheetsClient, auto_sync_job, delivery_log_job,
)
```

1b. Добавить в конец файла:

```python
# ---------------------------------------------------------------------------
# Часть Б: delivery_log_job — выгрузка отправленных задач в «Журнал отправок»
# ---------------------------------------------------------------------------

async def _make_delivery_instances(session_factory) -> dict[str, int]:
    """Три инстанса одного конфига: SENT (должен попасть в журнал), PENDING и
    FAILED (не должны). Возвращает {'sent': id, 'pending': id, 'failed': id}."""
    from bot.database.models import DeliveryStatus, Role
    from bot.database.repositories.task_repository import TaskRepository
    from bot.database.repositories.topic_repository import TopicRepository
    from bot.database.repositories.user_repository import UserRepository
    from bot.services.task_service import TaskService

    async with session_factory() as s:
        topic = await TopicRepository(s).upsert(topic_key="goods", topic_name="Товары",
                                                message_thread_id=10)
        user = await UserRepository(s).upsert(telegram_id=10, name="Валя",
                                              role=Role.MANAGER_WB)
        cfg = await TaskRepository(s).upsert_config(dict(
            external_task_id="log_me", title="Проверка артикулов", scenario="simple",
            schedule_type="daily", topic_id=topic.id, due_time=time(12, 0),
            responsible_user_id=user.id, is_active=True))
        svc = TaskService(s)
        sent = await svc.create_instance_for(cfg, datetime(2026, 7, 10, 9, 0))
        pending = await svc.create_instance_for(cfg, datetime(2026, 7, 11, 9, 0))
        failed = await svc.create_instance_for(cfg, datetime(2026, 7, 12, 9, 0))
        sent.delivery_status = DeliveryStatus.SENT
        sent.message_sent_at = datetime(2026, 7, 10, 9, 1)
        failed.delivery_status = DeliveryStatus.FAILED
        await s.commit()
        return {"sent": sent.id, "pending": pending.id, "failed": failed.id}


def _delivery_log_env():
    fake_settings = MagicMock(google_sheets_credentials_file="creds.json",
                              google_sheets_spreadsheet_id="sheet-id")
    fake_client = MagicMock()
    return fake_settings, fake_client


async def _enable_delivery_log(session_factory) -> None:
    async with session_factory() as s:
        await SettingService(s).set("delivery_log.enabled", True, actor_user_id=None)
        await s.commit()


async def test_delivery_log_job_skips_when_disabled(session_factory):
    from bot.database.models import TaskInstance

    ids = await _make_delivery_instances(session_factory)
    fake_settings, fake_client = _delivery_log_env()
    with patch("bot.services.google_sheets_service.SheetsClient",
               return_value=fake_client) as client_cls, \
         patch("bot.services.google_sheets_service.get_settings",
               return_value=fake_settings):
        await delivery_log_job(AsyncMock(), session_factory)   # enabled=False по умолчанию
    client_cls.assert_not_called()
    async with session_factory() as s:
        inst = await s.get(TaskInstance, ids["sent"])
        assert inst.sheet_logged_at is None


async def test_delivery_log_job_logs_only_sent_once(session_factory):
    """Только SENT попадает в журнал, ровно один раз (идемпотентность через
    sheet_logged_at), одним пакетным append_rows с корректной строкой."""
    from bot.database.models import TaskInstance

    ids = await _make_delivery_instances(session_factory)
    await _enable_delivery_log(session_factory)
    fake_settings, fake_client = _delivery_log_env()
    with patch("bot.services.google_sheets_service.SheetsClient",
               return_value=fake_client), \
         patch("bot.services.google_sheets_service.get_settings",
               return_value=fake_settings):
        await delivery_log_job(AsyncMock(), session_factory)
        await delivery_log_job(AsyncMock(), session_factory)   # повторный прогон

    fake_client.append_rows.assert_called_once()               # дублей нет
    sheet_name, rows = fake_client.append_rows.call_args.args
    assert sheet_name == "Журнал отправок"
    assert rows == [["Проверка артикулов", "Товары",
                     "10.07.2026 09:01", "10.07.2026 12:00"]]
    async with session_factory() as s:
        assert (await s.get(TaskInstance, ids["sent"])).sheet_logged_at is not None
        assert (await s.get(TaskInstance, ids["pending"])).sheet_logged_at is None
        assert (await s.get(TaskInstance, ids["failed"])).sheet_logged_at is None


async def test_delivery_log_job_error_keeps_rows_for_retry(session_factory):
    """Недоступность Sheets: исключение ловится, ни одна строка не помечена —
    на следующем интервале весь пакет уходит повторно."""
    from bot.database.models import TaskInstance

    ids = await _make_delivery_instances(session_factory)
    await _enable_delivery_log(session_factory)
    fake_settings, fake_client = _delivery_log_env()
    fake_client.append_rows.side_effect = RuntimeError("quota exceeded")
    with patch("bot.services.google_sheets_service.SheetsClient",
               return_value=fake_client), \
         patch("bot.services.google_sheets_service.get_settings",
               return_value=fake_settings):
        await delivery_log_job(AsyncMock(), session_factory)   # не должен упасть наружу
        async with session_factory() as s:
            assert (await s.get(TaskInstance, ids["sent"])).sheet_logged_at is None
        fake_client.append_rows.side_effect = None             # Sheets «ожил»
        await delivery_log_job(AsyncMock(), session_factory)
    assert fake_client.append_rows.call_count == 2             # ретрай состоялся
    async with session_factory() as s:
        assert (await s.get(TaskInstance, ids["sent"])).sheet_logged_at is not None
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `python -m pytest tests/test_sheets_service.py -q`
Expected: FAIL на этапе сбора — `ImportError: cannot import name 'delivery_log_job'`.

- [ ] **Step 3: Реализация**

В `bot/services/google_sheets_service.py` после функции `auto_sync_job` добавить:

```python
DELIVERY_LOG_SHEET_NAME = "Журнал отправок"
_DELIVERY_LOG_DT_FORMAT = "%d.%m.%Y %H:%M"


async def delivery_log_job(bot, session_factory) -> None:
    """Job-обработчик «Журнала отправок» (Часть Б; регистрируется
    SchedulerService.register_delivery_log_job). По строке на каждую
    фактически отправленную (delivery_status=SENT) TaskInstance, ещё не
    выгруженную (sheet_logged_at IS NULL): задача, чат/тема, время отправки,
    дедлайн (дата+время). Ничего не делает при delivery_log.enabled=False.

    Ошибка Google Sheets API (сеть/лимиты/credentials) логируется, ни одна
    строка НЕ помечается sheet_logged_at — весь пакет уйдёт повторно на
    следующем интервале. Отправка в Telegram уже случилась раньше и от
    успеха этого джоба не зависит."""
    from bot.database.db import async_session_factory
    from bot.database.repositories.task_repository import TaskRepository
    from bot.database.repositories.topic_repository import TopicRepository

    factory = session_factory or async_session_factory
    async with factory() as session:
        settings = SettingService(session)
        if not bool(await settings.get("delivery_log.enabled")):
            return
        task_repo = TaskRepository(session)
        topic_repo = TopicRepository(session)
        instances = await task_repo.get_sent_unlogged()
        if not instances:
            return
        rows: list[list] = []
        for inst in instances:
            topic_name = "—"
            if inst.topic_id is not None:
                topic = await topic_repo.get_by_id(inst.topic_id)
                topic_name = topic.topic_name if topic else "—"
            rows.append([
                inst.title_snapshot,
                topic_name,
                (inst.message_sent_at.strftime(_DELIVERY_LOG_DT_FORMAT)
                 if inst.message_sent_at else "—"),
                (inst.due_at.strftime(_DELIVERY_LOG_DT_FORMAT)
                 if inst.due_at else "—"),
            ])
        spreadsheet_id = (str(await settings.get("sync.spreadsheet_id"))
                          or get_settings().google_sheets_spreadsheet_id)
        try:
            client = SheetsClient(
                get_settings().google_sheets_credentials_file, spreadsheet_id)
            client.append_rows(DELIVERY_LOG_SHEET_NAME, rows)
        except Exception:  # noqa: BLE001 — недоступность Sheets не роняет планировщик
            logger.exception(
                "delivery_log_job: не удалось дописать %d строк(и) в лист %r — "
                "строки будут повторно взяты на следующем интервале",
                len(rows), DELIVERY_LOG_SHEET_NAME)
            return
        now = datetime.utcnow()
        for inst in instances:
            inst.sheet_logged_at = now
        await session.commit()
```

- [ ] **Step 4: Убедиться, что тесты проходят**

Run: `python -m pytest tests/test_sheets_service.py -q`
Expected: PASS.

- [ ] **Step 5: Интеграционный pg-тест (требование спеки, раздел «Тестирование»)**

Создать `tests/integration/test_delivery_log_pg.py`:

```python
"""Интеграционный тест «Журнала отправок» на реальном PostgreSQL: в журнал
попадают только SENT, повторный прогон не создаёт дублей (идемпотентность
через sheet_logged_at)."""
from datetime import datetime, time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.database.models import DeliveryStatus, Role, TaskInstance
from bot.database.repositories.task_repository import TaskRepository
from bot.database.repositories.topic_repository import TopicRepository
from bot.database.repositories.user_repository import UserRepository
from bot.services.google_sheets_service import delivery_log_job
from bot.services.setting_service import SettingService
from bot.services.task_service import TaskService

pytestmark = pytest.mark.pg


async def test_delivery_log_job_only_sent_and_idempotent_pg(pg_session_factory):
    async with pg_session_factory() as s:
        topic = await TopicRepository(s).upsert(topic_key="goods", topic_name="Товары")
        user = await UserRepository(s).upsert(telegram_id=10, name="Валя",
                                              role=Role.MANAGER_WB)
        cfg = await TaskRepository(s).upsert_config(dict(
            external_task_id="log_me_pg", title="Задача", scenario="simple",
            schedule_type="daily", topic_id=topic.id, due_time=time(12, 0),
            responsible_user_id=user.id, is_active=True))
        svc = TaskService(s)
        sent = await svc.create_instance_for(cfg, datetime(2026, 7, 10, 9, 0))
        pending = await svc.create_instance_for(cfg, datetime(2026, 7, 11, 9, 0))
        sent.delivery_status = DeliveryStatus.SENT
        sent.message_sent_at = datetime(2026, 7, 10, 9, 1)
        await SettingService(s).set("delivery_log.enabled", True, actor_user_id=None)
        await s.commit()
        sent_id, pending_id = sent.id, pending.id

    fake_settings = MagicMock(google_sheets_credentials_file="creds.json",
                              google_sheets_spreadsheet_id="sheet-id")
    fake_client = MagicMock()
    with patch("bot.services.google_sheets_service.SheetsClient",
               return_value=fake_client), \
         patch("bot.services.google_sheets_service.get_settings",
               return_value=fake_settings):
        await delivery_log_job(AsyncMock(), pg_session_factory)
        await delivery_log_job(AsyncMock(), pg_session_factory)   # повторный прогон

    fake_client.append_rows.assert_called_once()
    _sheet, rows = fake_client.append_rows.call_args.args
    assert len(rows) == 1
    async with pg_session_factory() as s:
        assert (await s.get(TaskInstance, sent_id)).sheet_logged_at is not None
        assert (await s.get(TaskInstance, pending_id)).sheet_logged_at is None
```

Run: `python -m pytest tests/integration/test_delivery_log_pg.py -m pg -q`
Expected: PASS при заданном `TEST_DATABASE_URL`; иначе «skipped» (это нормально).

- [ ] **Step 6: Полный юнит-прогон и коммит**

Run: `python -m pytest -q` — Expected: PASS.

```bash
git add bot/services/google_sheets_service.py tests/test_sheets_service.py tests/integration/test_delivery_log_pg.py
git commit -m "feat: delivery_log_job exports sent task instances to delivery log sheet"
```

---

### Task 8: регистрация `delivery_log`-джоба и связка с настройками

**Files:**
- Modify: `bot/services/scheduler_service.py` (новый метод после `register_sync_job`; функция `setup_scheduler`, ~строка 210)
- Modify: `bot/handlers/admin/settings.py` (`SCHEDULER_AFFECTING` ~строка 38; `apply_setting_input` ~строки 113-117)
- Test: `tests/test_scheduler_service.py`, `tests/test_admin_settings.py`

**Interfaces:**
- Consumes: `delivery_log_job(bot, session_factory)` (Task 7), настройки `delivery_log.*` (Task 6), паттерн `register_sync_job` (явный `remove_job` перед `add_job`, `GRACE`).
- Produces: `SchedulerService.register_delivery_log_job() -> None` (job id `"delivery_log"`, интервальный триггер, `args=[self.bot, self.session_factory]`, `misfire_grace_time=GRACE`); вызов при старте бота в `setup_scheduler`; редактирование `delivery_log.*` через любую карточку настроек пересобирает именно этот job.

- [ ] **Step 1: Написать падающие тесты**

1a. Добавить в конец `tests/test_scheduler_service.py`:

```python
async def test_register_delivery_log_job_enabled_interval_no_duplicates(session_factory):
    from bot.services.setting_service import SettingService

    async with session_factory() as s:
        await SettingService(s).set("delivery_log.enabled", True, actor_user_id=None)
        await SettingService(s).set("delivery_log.interval_minutes", 30,
                                    actor_user_id=None)
        await s.commit()

    scheduler = AsyncIOScheduler()
    svc = SchedulerService(scheduler, AsyncMock(), session_factory)
    for _ in range(3):                                 # идемпотентно, без дублей
        await svc.register_delivery_log_job()
    jobs = [j for j in scheduler.get_jobs() if j.id == "delivery_log"]
    assert len(jobs) == 1
    assert jobs[0].trigger.interval.total_seconds() == 30 * 60


async def test_register_delivery_log_job_disabled_removes_job(session_factory):
    from bot.services.setting_service import SettingService

    async with session_factory() as s:
        await SettingService(s).set("delivery_log.enabled", True, actor_user_id=None)
        await s.commit()
    scheduler = AsyncIOScheduler()
    svc = SchedulerService(scheduler, AsyncMock(), session_factory)
    await svc.register_delivery_log_job()
    assert scheduler.get_job("delivery_log") is not None

    async with session_factory() as s:
        await SettingService(s).set("delivery_log.enabled", False, actor_user_id=None)
        await s.commit()
    await svc.register_delivery_log_job()
    assert scheduler.get_job("delivery_log") is None    # выключили — job снят


async def test_setup_scheduler_registers_delivery_log_job(session_factory):
    from bot.services.scheduler_service import setup_scheduler
    from bot.services.setting_service import SettingService

    async with session_factory() as s:
        await SettingService(s).set("delivery_log.enabled", True, actor_user_id=None)
        await s.commit()
    scheduler = await setup_scheduler(AsyncMock(), session_factory)
    assert scheduler.get_job("delivery_log") is not None
```

1b. Добавить в конец `tests/test_admin_settings.py` (зеркало существующего `test_edit_value_validated_and_scheduler_rebuilt`; `make_config` и `AsyncMock` уже импортированы в файле):

```python
async def test_delivery_log_settings_rebuild_delivery_log_job(session):
    """Часть Б: правка delivery_log.* через общий редактор настроек
    пересобирает ИМЕННО delivery_log-job, а не sync-job."""
    cfg, valya = await make_config(session)
    from bot.handlers.admin.settings import SCHEDULER_AFFECTING, apply_setting_input

    assert "delivery_log.enabled" in SCHEDULER_AFFECTING
    assert "delivery_log.interval_minutes" in SCHEDULER_AFFECTING

    scheduler_svc = AsyncMock()
    ok, _ = await apply_setting_input(session, valya, "delivery_log.interval_minutes",
                                      "30", scheduler_svc)
    assert ok is True
    scheduler_svc.register_delivery_log_job.assert_awaited()
    scheduler_svc.register_sync_job.assert_not_awaited()
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `python -m pytest tests/test_scheduler_service.py tests/test_admin_settings.py -q`
Expected: FAIL — `AttributeError: 'SchedulerService' object has no attribute 'register_delivery_log_job'` (реальный сервис) и `assert "delivery_log.enabled" in SCHEDULER_AFFECTING`.

- [ ] **Step 3: Реализация**

3a. В `bot/services/scheduler_service.py` после метода `register_sync_job` добавить:

```python
    async def register_delivery_log_job(self) -> None:
        """Часть Б: по образцу register_sync_job — снимает job "delivery_log"
        и, если delivery_log.enabled=True, регистрирует delivery_log_job с
        интервалом delivery_log.interval_minutes. Явный remove_job перед
        add_job — см. комментарий в register_report_job (до scheduler.start()
        replace_existing не заменяет, а копит job'ы в _pending_jobs)."""
        from bot.services.google_sheets_service import delivery_log_job
        from bot.services.setting_service import SettingService
        async with self.session_factory() as session:
            settings = SettingService(session)
            enabled = bool(await settings.get("delivery_log.enabled"))
            minutes = int(await settings.get("delivery_log.interval_minutes"))
        if self.scheduler.get_job("delivery_log"):
            self.scheduler.remove_job("delivery_log")
        if enabled:
            self.scheduler.add_job(delivery_log_job, "interval", minutes=minutes,
                                   args=[self.bot, self.session_factory],
                                   id="delivery_log", misfire_grace_time=GRACE)
```

3b. В `setup_scheduler` того же файла заменить:

```python
    await svc.register_report_job()
    await svc.register_sync_job()
    return scheduler
```

на:

```python
    await svc.register_report_job()
    await svc.register_sync_job()
    await svc.register_delivery_log_job()
    return scheduler
```

3c. В `bot/handlers/admin/settings.py` заменить:

```python
SCHEDULER_AFFECTING = {"reports.weekday", "reports.time",
                       "sync.auto_enabled", "sync.interval_minutes"}
```

на:

```python
SCHEDULER_AFFECTING = {"reports.weekday", "reports.time",
                       "sync.auto_enabled", "sync.interval_minutes",
                       "delivery_log.enabled", "delivery_log.interval_minutes"}
```

3d. Там же, в `apply_setting_input`, заменить хвост ветки (после длинного комментария про «Коммит ДО пересборки job'а» — сам комментарий не трогать):

```python
        await session.commit()
        if key.startswith("reports."):
            await scheduler_svc.register_report_job()
        else:
            await scheduler_svc.register_sync_job()
```

на:

```python
        await session.commit()
        if key.startswith("reports."):
            await scheduler_svc.register_report_job()
        elif key.startswith("delivery_log."):
            await scheduler_svc.register_delivery_log_job()
        else:
            await scheduler_svc.register_sync_job()
```

(без новой ветки любые `delivery_log.*`-ключи из `SCHEDULER_AFFECTING` проваливались бы в sync-ветку — пересобирался бы не тот job).

- [ ] **Step 4: Убедиться, что тесты проходят**

Run: `python -m pytest tests/test_scheduler_service.py tests/test_admin_settings.py tests/test_admin_sync.py -q`
Expected: PASS.

- [ ] **Step 5: Полный юнит-прогон и коммит**

Run: `python -m pytest -q` — Expected: PASS.

```bash
git add bot/services/scheduler_service.py bot/handlers/admin/settings.py tests/test_scheduler_service.py tests/test_admin_settings.py
git commit -m "feat: register_delivery_log_job wired to startup and delivery_log settings"
```

---

## Часть В — независимые время отправки и дедлайн (+N дней)

### Task 9: колонка `TaskConfig.due_days_offset` + расчёт `due_at`

**Files:**
- Create: `bot/database/migrations/versions/0005_due_days_offset.py`
- Modify: `bot/database/models.py` (класс `TaskConfig`, после `due_time`, ~строка 168)
- Modify: `bot/services/task_service.py` (метод `create_instance_for`, строки 75-78)
- Test: `tests/test_task_service.py`

**Interfaces:**
- Consumes: существующие `TaskConfig.due_time`, `TaskService.create_instance_for(config, scheduled_at)`, фабрика `make_config` (задаёт `due_time=time(12, 0)`).
- Produces: `TaskConfig.due_days_offset: int` (default 0, `server_default="0"`, NOT NULL) — на сколько дней ПОСЛЕ дня отправки дедлайн; `0` — тот же день (текущее поведение). Расчёт: `due_at = datetime.combine(scheduled_at.date() + timedelta(days=config.due_days_offset), config.due_time)` при заданном `due_time`; без `due_time` — прежние `scheduled_at + 24h` (offset игнорируется). Tasks 10-11 пишут в это поле.

- [ ] **Step 1: Написать падающие тесты**

Добавить в конец `tests/test_task_service.py`:

```python
async def test_due_at_respects_due_days_offset(session):
    """Часть В: due_days_offset=2 — дедлайн через 2 дня после дня отправки."""
    cfg, _ = await make_config(session)                  # due_time=12:00
    cfg.due_days_offset = 2
    await session.commit()
    inst = await TaskService(session).create_instance_for(cfg, datetime(2026, 7, 10, 9, 0))
    assert inst.due_at == datetime(2026, 7, 12, 12, 0)


async def test_due_at_offset_default_zero_keeps_same_day(session):
    """default 0 — прежнее поведение «сегодня до HH:MM» (обратная совместимость)."""
    cfg, _ = await make_config(session)
    assert cfg.due_days_offset == 0
    inst = await TaskService(session).create_instance_for(cfg, datetime(2026, 7, 10, 9, 0))
    assert inst.due_at == datetime(2026, 7, 10, 12, 0)


async def test_due_at_without_due_time_ignores_offset(session):
    """Без due_time дедлайн — прежние +24 часа, offset не участвует."""
    cfg, _ = await make_config(session)
    cfg.due_time = None
    cfg.due_days_offset = 5
    await session.commit()
    inst = await TaskService(session).create_instance_for(cfg, datetime(2026, 7, 10, 9, 0))
    assert inst.due_at == datetime(2026, 7, 11, 9, 0)
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `python -m pytest tests/test_task_service.py -q`
Expected: FAIL — у `TaskConfig` нет атрибута `due_days_offset`.

- [ ] **Step 3: Реализация**

3a. В `bot/database/models.py`, в классе `TaskConfig`, после строки `due_time: Mapped[time | None] = mapped_column(Time, nullable=True)  # срок «сегодня до HH:MM»` добавить:

```python
    # Часть В: дедлайн через N дней ПОСЛЕ дня отправки; 0 — тот же день
    # (прежнее поведение). server_default="0" — существующие строки получают 0.
    due_days_offset: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
```

3b. В `bot/services/task_service.py`, в `create_instance_for`, заменить:

```python
        due_at = (datetime.combine(scheduled_at.date(), config.due_time)
                  if config.due_time else scheduled_at + timedelta(hours=24))
```

на:

```python
        due_at = (datetime.combine(
                      scheduled_at.date() + timedelta(days=config.due_days_offset),
                      config.due_time)
                  if config.due_time else scheduled_at + timedelta(hours=24))
```

3c. Создать `bot/database/migrations/versions/0005_due_days_offset.py`:

```python
"""add due_days_offset to tasks_config

Revision ID: 0005
Revises: 0004
Create Date: 2026-07-14 12:10:00.000000

"""
import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"


def upgrade() -> None:
    op.add_column(
        "tasks_config",
        sa.Column("due_days_offset", sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    op.drop_column("tasks_config", "due_days_offset")
```

- [ ] **Step 4: Убедиться, что тесты проходят**

Run: `python -m pytest tests/test_task_service.py tests/test_models.py -q`
Expected: PASS (в т.ч. старый `test_due_at_from_due_time` — offset по умолчанию 0).

- [ ] **Step 5: Проверка миграции (если доступен тестовый Postgres)**

Run: `python -m pytest tests/integration/test_alembic_migration.py -m pg -q`
Expected: PASS при заданном `TEST_DATABASE_URL`; иначе «skipped».

- [ ] **Step 6: Полный юнит-прогон и коммит**

Run: `python -m pytest -q` — Expected: PASS.

```bash
git add bot/database/models.py bot/services/task_service.py bot/database/migrations/versions/0005_due_days_offset.py tests/test_task_service.py
git commit -m "feat: TaskConfig.due_days_offset shifts due_at N days after send day"
```

---

### Task 10: синк читает `time` из собственной колонки + `due_days_offset` из листа

**Files:**
- Modify: `bot/services/google_sheets_service.py` (метод `sync_tasks`, словарь `data`, ~строки 308-317)
- Test: `tests/test_sheets_service.py` (константа `TASK_ROWS` ~строка 165 и новые тесты)

**Interfaces:**
- Consumes: `_parse_time`, `_moscow_to_utc`, `_int_or_none` (все уже в модуле), `TaskConfig.due_days_offset` (Task 9).
- Produces: `sync_tasks()` читает `TaskConfig.time` из колонки листа `time` (МСК → UTC), `due_time` — из своей колонки `due_time` (как раньше, без конвертации), `due_days_offset` — из колонки `due_days_offset` (пустая ячейка / отсутствие колонки → 0). ВНИМАНИЕ: `time` перестаёт выводиться из ячейки `due_time` — требуется ручной шаг с реальной таблицей (см. «Ручной шаг» в конце плана).

- [ ] **Step 1: Обновить фикстуру и написать падающие тесты**

1a. В `tests/test_sheets_service.py` заменить константу `TASK_ROWS`:

```python
TASK_ROWS = [
    {"external_task_id": "articles_check_all", "title": "Проверка артикулов",
     "scenario": "article_check", "schedule_type": "daily", "schedule_interval": "",
     "time": "18:00", "due_time": "18:00", "due_days_offset": "", "active": "1"},
]
```

(значения `time` и `due_time` совпадают, поэтому существующие asserts `due_time == 18:00:00` / `time == 15:00:00 UTC` в `test_sync_tasks_add_and_deactivate` верны и ДО, и ПОСЛЕ реализации — фикстура не ломает ни старое, ни новое поведение.)

1b. Добавить в конец файла:

```python
async def test_sync_tasks_time_and_due_time_independent(session_factory):
    """Часть В: time (отправка) и due_time (дедлайн) — независимые колонки
    листа; due_days_offset читается из своей колонки."""
    row = [{"external_task_id": "independent", "title": "Задача", "scenario": "simple",
            "schedule_type": "daily", "time": "10:00", "due_time": "18:00",
            "due_days_offset": "2", "active": "1"}]
    async with session_factory() as s:
        svc = GoogleSheetsService(s, client=None)
        await svc.sync_tasks(row, dry_run=False)
        await s.commit()
        cfg = await s.scalar(select(TaskConfig).where(
            TaskConfig.external_task_id == "independent"))
        assert cfg.time.isoformat() == "07:00:00"       # 10:00 МСК -> 07:00 UTC
        assert cfg.due_time.isoformat() == "18:00:00"   # дедлайн — как в таблице, МСК
        assert cfg.due_days_offset == 2


async def test_sync_tasks_missing_new_columns_default(session_factory):
    """Обратная совместимость: без колонок time/due_days_offset — time=None
    (планировщик подставит дефолт 09:00), offset=0 (дедлайн в день отправки)."""
    row = [{"external_task_id": "no_new_columns", "title": "Задача",
            "scenario": "simple", "schedule_type": "daily",
            "due_time": "18:00", "active": "1"}]
    async with session_factory() as s:
        svc = GoogleSheetsService(s, client=None)
        await svc.sync_tasks(row, dry_run=False)
        await s.commit()
        cfg = await s.scalar(select(TaskConfig).where(
            TaskConfig.external_task_id == "no_new_columns"))
        assert cfg.time is None                         # больше НЕ выводится из due_time
        assert cfg.due_time.isoformat() == "18:00:00"
        assert cfg.due_days_offset == 0
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `python -m pytest tests/test_sheets_service.py::test_sync_tasks_time_and_due_time_independent tests/test_sheets_service.py::test_sync_tasks_missing_new_columns_default -q`
Expected: FAIL — сейчас `cfg.time` выводится из `due_time`: в первом тесте `15:00:00` вместо `07:00:00`, во втором `15:00:00` вместо `None`; `due_days_offset` не читается вовсе.

- [ ] **Step 3: Реализация**

В `bot/services/google_sheets_service.py`, в `sync_tasks()`, заменить блок:

```python
                        # time — момент, когда планировщик реально создаёт/отправляет
                        # задачу (compute_next_run, bot/services/scheduler_service.py,
                        # работает в naive-UTC) — иначе задача всегда уходила бы в
                        # 09:00 по умолчанию независимо от due_time в таблице.
                        # due_time в самой таблице — московское время, поэтому для
                        # time конвертируем в UTC; due_time (дедлайн, показывается
                        # сотрудникам текстом) оставляем как есть — по МСК, как
                        # написано в таблице, это и должно быть видно человеку.
                        "time": _moscow_to_utc(_parse_time(row.get("due_time"))),
                        "due_time": _parse_time(row.get("due_time")),
```

на:

```python
                        # time — момент, когда планировщик реально создаёт/отправляет
                        # задачу (compute_next_run, bot/services/scheduler_service.py,
                        # работает в naive-UTC). Часть В: читается из СОБСТВЕННОЙ
                        # колонки листа `time` (МСК -> UTC); раньше вычислялся из
                        # ячейки due_time — колонки были искусственно склеены.
                        # due_time (дедлайн, показывается сотрудникам текстом) —
                        # из своей колонки, без конвертации: по МСК, как в таблице.
                        # due_days_offset — дедлайн через N дней после дня отправки;
                        # пустая ячейка/нет колонки -> 0 (тот же день, как раньше).
                        "time": _moscow_to_utc(_parse_time(row.get("time"))),
                        "due_time": _parse_time(row.get("due_time")),
                        "due_days_offset": _int_or_none(row.get("due_days_offset")) or 0,
```

- [ ] **Step 4: Убедиться, что тесты проходят**

Run: `python -m pytest tests/test_sheets_service.py -q`
Expected: PASS (в т.ч. `test_sync_tasks_add_and_deactivate` с обновлённой фикстурой).

- [ ] **Step 5: Полный юнит-прогон и коммит**

Run: `python -m pytest -q` — Expected: PASS.

```bash
git add bot/services/google_sheets_service.py tests/test_sheets_service.py
git commit -m "feat: sheet sync reads send time and due_days_offset from own columns"
```

---

### Task 11: редактирование `due_days_offset` в админ-панели «📅 Расписания»

**Files:**
- Modify: `bot/handlers/admin/schedules.py` (`SCHEDULE_FIELDS` ~строка 75, `SCHEDULE_FIELD_LIST` ~строка 81, `FIELD_TITLES` ~строка 86, `_parse` ~строка 101, `render_schedule_card` ~строка 160, `_hint_for` ~строка 319)
- Test: `tests/test_admin_schedules.py`

**Interfaces:**
- Consumes: `TaskConfig.due_days_offset` (Task 9), `validate_int(raw, min_, max_)` из `bot/utils/validation.py`, `apply_schedule_field` (уже коммитит внутри себя и вызывает `rebuild_config_job` — дополнительной работы не нужно; whitelist по `SCHEDULE_FIELDS`).
- Produces: поле `due_days_offset` редактируется через кнопки раздела «📅 Расписания», валидация 0..30. В `SCHEDULE_FIELD_LIST` добавляется В КОНЕЦ — редактирование идёт по индексу в этом списке, вставка в середину сдвинула бы индексы существующих полей.

- [ ] **Step 1: Написать падающие тесты**

Добавить в конец `tests/test_admin_schedules.py` (хелпер `_owner` и `make_config` уже есть в файле):

```python
def test_due_days_offset_registered_in_field_lists():
    from bot.handlers.admin.schedules import (
        FIELD_TITLES, SCHEDULE_FIELD_LIST, SCHEDULE_FIELDS,
    )
    assert "due_days_offset" in SCHEDULE_FIELDS
    # добавлено строго В КОНЕЦ: вставка в середину сдвинула бы индексы
    # whitelist-редактирования по idx в SCHEDULE_FIELD_LIST
    assert SCHEDULE_FIELD_LIST[-1] == "due_days_offset"
    assert FIELD_TITLES["due_days_offset"] == "Срок: дней после отправки"


async def test_due_days_offset_valid_value_applies(session):
    owner = await _owner(session)
    cfg, _ = await make_config(session)
    from bot.handlers.admin.schedules import apply_schedule_field
    ok, _ = await apply_schedule_field(session, owner, cfg.id, "due_days_offset", "2", None)
    assert ok is True and cfg.due_days_offset == 2


async def test_due_days_offset_rejects_out_of_range(session):
    owner = await _owner(session)
    cfg, _ = await make_config(session)
    from bot.handlers.admin.schedules import apply_schedule_field
    ok, _ = await apply_schedule_field(session, owner, cfg.id, "due_days_offset", "-1", None)
    assert ok is False
    ok, _ = await apply_schedule_field(session, owner, cfg.id, "due_days_offset", "31", None)
    assert ok is False
    assert cfg.due_days_offset == 0                       # значение не тронуто


async def test_schedule_card_shows_due_days_offset(session):
    cfg, _ = await make_config(session)
    cfg.due_days_offset = 3
    await session.commit()
    from bot.handlers.admin.schedules import render_schedule_card
    assert "Срок: дней после отправки: 3" in render_schedule_card(cfg)
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `python -m pytest tests/test_admin_schedules.py -q`
Expected: FAIL — `"due_days_offset" in SCHEDULE_FIELDS` = False; `apply_schedule_field` возвращает `(False, "Поле недоступно для редактирования расписания")`.

- [ ] **Step 3: Реализация**

В `bot/handlers/admin/schedules.py`:

3a. Заменить `SCHEDULE_FIELDS`:

```python
SCHEDULE_FIELDS: frozenset[str] = frozenset({
    "schedule_type", "schedule_value", "schedule_interval", "time", "due_time",
    "due_days_offset", "first_run_date", "run_on_weekends", "skip_holidays",
})
```

3b. Заменить `SCHEDULE_FIELD_LIST` (новое поле строго В КОНЦЕ, см. Interfaces):

```python
SCHEDULE_FIELD_LIST: list[str] = [
    "schedule_type", "schedule_value", "schedule_interval", "time", "due_time",
    "first_run_date", "run_on_weekends", "skip_holidays", "due_days_offset",
]
```

3c. В `FIELD_TITLES`, после строки `"due_time": "Срок (due_time)",` добавить:

```python
    "due_days_offset": "Срок: дней после отправки",
```

3d. В `_parse`, после ветки `if field == "schedule_interval": return validate_int(raw, 1, 365)` добавить:

```python
    if field == "due_days_offset":
        return validate_int(raw, 0, 30)                 # 0 — дедлайн в день отправки
```

3e. В `render_schedule_card`, после элемента списка `f"Срок (due_time): {cfg.due_time.strftime('%H:%M') if cfg.due_time else '—'}",` добавить элемент:

```python
        f"Срок: дней после отправки: {cfg.due_days_offset}",
```

3f. В `_hint_for`, после ветки `if field in ("time", "due_time"): return "Введите время в формате ЧЧ:ММ:"` добавить:

```python
    if field == "due_days_offset":
        return "Введите число дней после дня отправки (0-30, 0 — тот же день):"
```

- [ ] **Step 4: Убедиться, что тесты проходят**

Run: `python -m pytest tests/test_admin_schedules.py -q`
Expected: PASS.

- [ ] **Step 5: Полный юнит-прогон и коммит**

Run: `python -m pytest -q` — Expected: PASS.

```bash
git add bot/handlers/admin/schedules.py tests/test_admin_schedules.py
git commit -m "feat: due_days_offset editable in schedules admin section (0-30)"
```

---

## Ручной шаг вне кода (НЕ задача плана — для него нет и не должно быть кода)

После деплоя этих изменений администратор должен один раз вручную добавить в реальную Google-таблицу, в лист задач (настройка `sync.sheet_tasks`, по умолчанию `Tasks_Config`), **две новые колонки** с заголовками, ТОЧНО совпадающими со строками:

- `time` — время отправки задачи, формат `ЧЧ:ММ`, московское время (конвертацию в UTC делает бот);
- `due_days_offset` — целое число дней (0-30): на сколько дней после дня отправки наступает дедлайн; пустая ячейка = 0 (дедлайн в день отправки, как раньше).

gspread `get_all_records()` использует строку заголовков листа как ключи словаря — текст заголовка обязан совпадать посимвольно. Пока колонок нет: `due_days_offset` = 0 (поведение не меняется), а `time` станет `None`, и планировщик подставит дефолт 09:00 UTC — поэтому колонку `time` следует заполнить (например, прежними значениями из `due_time`) **сразу** при добавлении, до следующего синка. Лист «Журнал отправок» вручную создавать не нужно — `append_rows` создаст его сам с заголовком при первой выгрузке.

Также сервисному аккаунту Google нужны права **редактора** таблицы (scope стал read-write; если аккаунт добавлен как «читатель», запись в журнал будет падать и логироваться до выдачи прав — на отправку сообщений в Telegram это не влияет).

## Порядок применения миграций при деплое

`alembic upgrade head` (накатит `0004`, затем `0005`). Обе миграции обратимы (`downgrade` удаляет колонку).
