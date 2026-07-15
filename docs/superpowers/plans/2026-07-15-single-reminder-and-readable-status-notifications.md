# Единое напоминание + человекочитаемые статусы Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the three-part reminder mechanism (`remind1`/`remind2`/`overdue`-with-escalation) with exactly one reminder — `not_taken_reminder_job`, firing `reminders.not_taken_after_hours` (default 12) hours after send if the task is still `CREATED` — and make owner status-change notifications show human-readable Russian labels instead of raw enum values.

**Architecture:** Two independent, small changes to existing bot code (no new files except tests): (1) a new one-shot APScheduler job replacing two existing job functions, registered from the same two call sites that already register per-instance jobs (`register_instance_jobs`, `recover_jobs`); (2) a one-line fix reusing an already-existing label dictionary.

**Tech Stack:** Existing bot stack (aiogram3, SQLAlchemy async, APScheduler, pytest + SQLite in-memory `session_factory` fixture). No new dependencies.

## Global Constraints

- DB columns and admin-panel/web-admin edit forms for the old mechanism (`TaskConfig.remind_after_hours`, `TaskConfig.second_remind_after_hours`, `TaskInstance.remind_after_hours_snapshot`, `TaskInstance.second_remind_after_hours_snapshot`, `TaskInstance.reminders_sent`, and settings `reminders.overdue_after_hours`, `reminders.overdue_enabled`, `reminders.escalation_enabled`, `reminders.escalation_targets`, `reminders.max_count`, `reminders.quiet_hours_*`, `reminders.shift_night_to_morning`, `reminders.targets`, `reminders.text_template`) are NOT touched, NOT migrated, NOT removed from any form. Only the logic that reads them for scheduling purposes is removed.
- `reminder_job` and `overdue_job` (in `bot/services/reminder_service.py`) are deleted entirely — by the end of this plan they have zero callers anywhere in the codebase.
- No new Alembic migration in this plan (no schema change).
- Every DB-touching test reuses the existing `session_factory` fixture (SQLite in-memory) from `tests/conftest.py`.

---

### Task 1: New setting + human-readable status labels

**Files:**
- Modify: `bot/services/setting_service.py`
- Modify: `bot/services/status_notification_service.py`
- Modify: `tests/test_status_notification_service.py`

**Interfaces:**
- Produces: setting key `"reminders.not_taken_after_hours"` (int, default `12`) — consumed by Task 2/3/4's job registration.

- [ ] **Step 1: Modify `bot/services/setting_service.py`** — add the new setting

In the `--- reminders ---` section (currently lines 124-157), add a new `SettingDef` right after `reminders.second_after_hours` (or anywhere in that block — exact position doesn't matter, category grouping does):
```python
        SettingDef("reminders.not_taken_after_hours", int, 12, "reminders",
                   "Через сколько часов слать единственное напоминание, если задачу "
                   "так и не взяли в работу", min_=1, max_=72),
```

- [ ] **Step 2: Modify `bot/services/status_notification_service.py`** — use human-readable labels

Add import at the top:
```python
from bot.utils.message_templates import STATUS_LABELS
```

Replace:
```python
            text = (f"📌 {html_escape(title)}: {log.old_status or '—'} → "
                    f"{log.new_status} — {html_escape(who)}")
```
with:
```python
            old_label = STATUS_LABELS.get(log.old_status, log.old_status) if log.old_status else "—"
            new_label = STATUS_LABELS.get(log.new_status, log.new_status)
            text = (f"📌 {html_escape(title)}: {old_label} → "
                    f"{new_label} — {html_escape(who)}")
```

- [ ] **Step 3: Update the now-stale assertions in `tests/test_status_notification_service.py`**

Replace (line 85):
```python
    assert any("Проверка" in t and "created → in_progress" in t and "Валя" in t
               for t in texts)
```
with:
```python
    assert any("Проверка" in t and "🆕 Создана → 🔄 В работе" in t and "Валя" in t
               for t in texts)
```

Replace (line 87):
```python
    assert any("in_progress → overdue" in t and "auto:overdue" in t for t in texts)
```
with:
```python
    assert any("🔄 В работе → 🔥 Просрочена" in t and "auto:overdue" in t for t in texts)
```

Replace (line 88):
```python
    assert any("in_progress → completed" in t for t in texts)
```
with:
```python
    assert any("🔄 В работе → ✅ Выполнена" in t for t in texts)
```

Replace (line 106):
```python
    assert "in_progress → completed" in bot.send_message.await_args.kwargs["text"]
```
with:
```python
    assert "🔄 В работе → ✅ Выполнена" in bot.send_message.await_args.kwargs["text"]
```

Replace (line 116):
```python
    assert "waiting_approval" in bot.send_message.await_args.kwargs["text"]
```
with:
```python
    assert "⏳ Ждет подтверждения" in bot.send_message.await_args.kwargs["text"]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_status_notification_service.py -v`
Expected: `6 passed`

- [ ] **Step 5: Commit**

```bash
git add bot/services/setting_service.py bot/services/status_notification_service.py tests/test_status_notification_service.py
git commit -m "feat: reminders.not_taken_after_hours setting; human-readable status labels in owner notifications"
```

---

### Task 2: `not_taken_reminder_job` replaces `reminder_job`/`overdue_job`

**Files:**
- Modify: `bot/services/reminder_service.py`
- Test: `tests/test_reminder_service.py` (full rewrite — old tests exercise functions being deleted)

**Interfaces:**
- Consumes: `bot.database.models.TaskStatus`, `bot.database.repositories.task_repository.TaskRepository`, `bot.utils.html_utils.{bold, html_escape, mention}`, `bot.services.setting_service.SettingService` (existing).
- Produces: `not_taken_reminder_job(instance_id: int, bot, session_factory) -> None` — consumed by Task 3 (`register_instance_jobs`) and Task 4 (`recover_jobs`).

- [ ] **Step 1: Write the failing tests**

Replace the entire contents of `tests/test_reminder_service.py`:
```python
from datetime import datetime
from unittest.mock import AsyncMock

from bot.database.models import Role, TaskStatus
from bot.database.repositories.task_repository import TaskRepository
from bot.database.repositories.user_repository import UserRepository
from bot.services.reminder_service import not_taken_reminder_job
from bot.services.setting_service import SettingService


async def seed_instance(session_factory, status=TaskStatus.CREATED):
    async with session_factory() as s:
        user = await UserRepository(s).upsert(telegram_id=10, name="Валя",
                                              role=Role.MANAGER_WB)
        repo = TaskRepository(s)
        cfg = await repo.upsert_config(dict(
            external_task_id="t", title="Проверка", scenario="article_check",
            schedule_type="every_n_days", schedule_interval=2,
            responsible_user_id=user.id, is_active=True))
        inst = await repo.create_instance_idempotent(
            cfg, datetime(2026, 7, 10, 9), datetime(2026, 7, 10, 12),
            dict(title_snapshot="Проверка", scenario_snapshot="article_check",
                 topic_snapshot=42, responsible_telegram_id_snapshot=10,
                 responsible_name_snapshot="Валя"))
        if status != TaskStatus.CREATED:
            await repo.transition_status(inst.id, [TaskStatus.CREATED], status, None, "x")
        await s.commit()
        return inst.id


async def test_sends_one_message_when_still_created(session_factory):
    inst_id = await seed_instance(session_factory)
    async with session_factory() as s:
        await SettingService(s).set("reminders.not_taken_after_hours", 12,
                                    actor_user_id=None)
        await s.commit()
    bot = AsyncMock()
    await not_taken_reminder_job(inst_id, bot, session_factory)
    bot.send_message.assert_awaited_once()
    kwargs = bot.send_message.await_args.kwargs
    assert kwargs["message_thread_id"] == 42
    assert "Проверка" in kwargs["text"]
    assert "12" in kwargs["text"]              # использует реальное значение настройки
    assert "не взята в работу" in kwargs["text"]


async def test_uses_custom_setting_value_in_text(session_factory):
    inst_id = await seed_instance(session_factory)
    async with session_factory() as s:
        await SettingService(s).set("reminders.not_taken_after_hours", 6,
                                    actor_user_id=None)
        await s.commit()
    bot = AsyncMock()
    await not_taken_reminder_job(inst_id, bot, session_factory)
    text = bot.send_message.await_args.kwargs["text"]
    assert "6" in text
    assert "12" not in text


async def test_skipped_when_already_in_progress(session_factory):
    inst_id = await seed_instance(session_factory, TaskStatus.IN_PROGRESS)
    bot = AsyncMock()
    await not_taken_reminder_job(inst_id, bot, session_factory)
    bot.send_message.assert_not_awaited()


async def test_skipped_when_completed(session_factory):
    inst_id = await seed_instance(session_factory, TaskStatus.COMPLETED)
    bot = AsyncMock()
    await not_taken_reminder_job(inst_id, bot, session_factory)
    bot.send_message.assert_not_awaited()


async def test_skipped_when_instance_missing(session_factory):
    bot = AsyncMock()
    await not_taken_reminder_job(999999, bot, session_factory)
    bot.send_message.assert_not_awaited()


async def test_send_failure_does_not_raise(session_factory):
    inst_id = await seed_instance(session_factory)
    bot = AsyncMock()
    bot.send_message.side_effect = RuntimeError("bot was blocked by the user")
    await not_taken_reminder_job(inst_id, bot, session_factory)   # не должен упасть наружу
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_reminder_service.py -v`
Expected: FAIL with `ImportError: cannot import name 'not_taken_reminder_job' from 'bot.services.reminder_service'`

- [ ] **Step 3: Replace the entire contents of `bot/services/reminder_service.py`**

```python
"""Job-обработчик единственного напоминания «задача не взята в работу»
(заменяет прежние reminder_job/remind1/remind2 и overdue_job/эскалацию).

Одноразовый job (APScheduler trigger="date") сам по себе гарантирует, что
сообщение уйдёт максимум один раз на инстанс — никакого счётчика отправленных
напоминаний не требуется.
"""
from bot.database.models import TaskStatus
from bot.database.repositories.task_repository import TaskRepository
from bot.services.setting_service import SettingService
from bot.utils.html_utils import bold, html_escape, mention
from bot.utils.logger import get_logger

logger = get_logger(__name__)


async def not_taken_reminder_job(instance_id: int, bot, session_factory) -> None:
    async with session_factory() as session:
        repo = TaskRepository(session)
        inst = await repo.get_instance(instance_id)
        if inst is None or inst.status != TaskStatus.CREATED:
            return
        settings = SettingService(session)
        hours = int(await settings.get("reminders.not_taken_after_hours"))
        if inst.responsible_telegram_id_snapshot is not None:
            who = mention(inst.responsible_telegram_id_snapshot,
                          inst.responsible_name_snapshot)
        elif inst.responsible_name_snapshot:
            who = html_escape(inst.responsible_name_snapshot)
        else:
            who = "—"
        text = (f"⏰ Задача {bold(inst.title_snapshot)} всё ещё не взята в работу "
                f"({hours}ч). Ответственный: {who}")
        chat_id = int(await settings.get("general.group_chat_id"))
        try:
            await bot.send_message(chat_id=chat_id,
                                   message_thread_id=inst.topic_snapshot, text=text)
        except Exception as exc:                      # noqa: BLE001 — не рушим job
            logger.warning("Not-taken reminder send failed for instance=%s: %s",
                           instance_id, exc)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_reminder_service.py -v`
Expected: `6 passed`

- [ ] **Step 5: Commit**

```bash
git add bot/services/reminder_service.py tests/test_reminder_service.py
git commit -m "feat: not_taken_reminder_job replaces reminder_job/overdue_job (single reminder at 12h)"
```

---

### Task 3: Wire `not_taken_reminder_job` into `register_instance_jobs`

**Files:**
- Modify: `bot/services/scheduler_service.py`
- Modify: `tests/test_scheduler_service.py`

**Interfaces:**
- Consumes: `bot.services.reminder_service.not_taken_reminder_job` (Task 2).

- [ ] **Step 1: Write the failing test**

Replace (in `tests/test_scheduler_service.py`) the two tests `test_register_instance_jobs_overdue_uses_setting` and `test_register_instance_jobs_overdue_default_24` (currently lines 346-401) with:
```python
async def test_register_instance_jobs_not_taken_uses_setting(session_factory):
    """not_taken:{id} планируется через reminders.not_taken_after_hours (здесь 2)."""
    from datetime import timedelta
    from bot.services.setting_service import SettingService

    async with session_factory() as s:
        user = await UserRepository(s).upsert(telegram_id=7, name="Валя",
                                              role=Role.MANAGER_WB)
        repo = TaskRepository(s)
        cfg = await repo.upsert_config(dict(
            external_task_id="not_taken_from_setting", title="Проверка",
            scenario="simple", schedule_type="daily",
            responsible_user_id=user.id, is_active=True))
        inst = await repo.create_instance_idempotent(
            cfg, datetime(2026, 7, 10, 9, 0), None,
            dict(title_snapshot="Проверка", scenario_snapshot="simple"))
        await SettingService(s).set("reminders.not_taken_after_hours", 2,
                                    actor_user_id=None)
        await s.commit()

    scheduler = AsyncIOScheduler(timezone="UTC")     # как в проде (setup_scheduler)
    svc = SchedulerService(scheduler, AsyncMock(), session_factory)
    await svc.register_instance_jobs(inst)

    job = scheduler.get_job(f"not_taken:{inst.id}")
    assert job is not None
    assert job.trigger.run_date.replace(tzinfo=None) == \
        datetime(2026, 7, 10, 9, 0) + timedelta(hours=2)     # НЕ дефолтные 12
    assert scheduler.get_job(f"remind1:{inst.id}") is None
    assert scheduler.get_job(f"remind2:{inst.id}") is None
    assert scheduler.get_job(f"overdue:{inst.id}") is None


async def test_register_instance_jobs_not_taken_default_12(session_factory):
    """Без явной настройки — дефолт 12 часов (реестр reminders.not_taken_after_hours)."""
    from datetime import timedelta

    async with session_factory() as s:
        user = await UserRepository(s).upsert(telegram_id=8, name="Валя",
                                              role=Role.MANAGER_WB)
        repo = TaskRepository(s)
        cfg = await repo.upsert_config(dict(
            external_task_id="not_taken_default", title="Проверка",
            scenario="simple", schedule_type="daily",
            responsible_user_id=user.id, is_active=True))
        inst = await repo.create_instance_idempotent(
            cfg, datetime(2026, 7, 10, 9, 0), None,
            dict(title_snapshot="Проверка", scenario_snapshot="simple"))
        await s.commit()

    scheduler = AsyncIOScheduler(timezone="UTC")
    svc = SchedulerService(scheduler, AsyncMock(), session_factory)
    await svc.register_instance_jobs(inst)
    job = scheduler.get_job(f"not_taken:{inst.id}")
    assert job.trigger.run_date.replace(tzinfo=None) == \
        datetime(2026, 7, 10, 9, 0) + timedelta(hours=12)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_scheduler_service.py -k not_taken -v`
Expected: FAIL — `not_taken:{id}` job not found (old code still registers `remind1`/`remind2`/`overdue`)

- [ ] **Step 3: Modify `bot/services/scheduler_service.py`** — replace the job registration block in `register_instance_jobs`

Replace (currently lines 159-184, the whole body from the `reminder_job, overdue_job` import through the final `overdue_job` registration — this is the ENTIRE remainder of the method, do not stop partway):
```python
        from bot.services.reminder_service import reminder_job, overdue_job
        from bot.services.setting_service import SettingService
        if session is not None:
            overdue_hours = int(await SettingService(session).get(
                "reminders.overdue_after_hours"))
        else:
            async with self.session_factory() as own_session:
                overdue_hours = int(await SettingService(own_session).get(
                    "reminders.overdue_after_hours"))
        base = inst.scheduled_at
        if inst.remind_after_hours_snapshot:
            self.scheduler.add_job(
                reminder_job, "date",
                run_date=base + timedelta(hours=inst.remind_after_hours_snapshot),
                args=[inst.id, 1, self.bot, self.session_factory, self.scheduler],
                id=f"remind1:{inst.id}", replace_existing=True, misfire_grace_time=GRACE)
        if inst.second_remind_after_hours_snapshot:
            self.scheduler.add_job(
                reminder_job, "date",
                run_date=base + timedelta(hours=inst.second_remind_after_hours_snapshot),
                args=[inst.id, 2, self.bot, self.session_factory, self.scheduler],
                id=f"remind2:{inst.id}", replace_existing=True, misfire_grace_time=GRACE)
        self.scheduler.add_job(
            overdue_job, "date", run_date=base + timedelta(hours=overdue_hours),
            args=[inst.id, self.bot, self.session_factory],
            id=f"overdue:{inst.id}", replace_existing=True, misfire_grace_time=GRACE)
```
with:
```python
        from bot.services.reminder_service import not_taken_reminder_job
        from bot.services.setting_service import SettingService
        if session is not None:
            not_taken_hours = int(await SettingService(session).get(
                "reminders.not_taken_after_hours"))
        else:
            async with self.session_factory() as own_session:
                not_taken_hours = int(await SettingService(own_session).get(
                    "reminders.not_taken_after_hours"))
        base = inst.scheduled_at
        self.scheduler.add_job(
            not_taken_reminder_job, "date",
            run_date=base + timedelta(hours=not_taken_hours),
            args=[inst.id, self.bot, self.session_factory],
            id=f"not_taken:{inst.id}", replace_existing=True, misfire_grace_time=GRACE)
```

Also update the docstring line `"""Регистрирует remind1/remind2/overdue job'ы инстанса.` to `"""Регистрирует not_taken job инстанса (единственное напоминание "не взято в работу").` — keep the rest of the existing docstring below it (the explanation of the `session` parameter) unchanged, it's still accurate.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_scheduler_service.py -v`
Expected: all pass, including the 2 new `not_taken` tests (old overdue tests removed, count decreases by 0 net — 2 removed, 2 added)

- [ ] **Step 5: Commit**

```bash
git add bot/services/scheduler_service.py tests/test_scheduler_service.py
git commit -m "feat: register_instance_jobs registers single not_taken job instead of remind1/remind2/overdue"
```

---

### Task 4: Wire `not_taken_reminder_job` into `recover_jobs` (bot restart recovery)

**Files:**
- Modify: `bot/services/scheduler_recovery_service.py`
- Modify: `tests/test_scheduler_recovery.py`

**Interfaces:**
- Consumes: `bot.services.reminder_service.not_taken_reminder_job` (Task 2).

- [ ] **Step 1: Write the failing test**

Replace (in `tests/test_scheduler_recovery.py`) lines 51-52:
```python
    assert f"remind1:{inst_id}" in job_ids and f"remind2:{inst_id}" in job_ids
    assert f"overdue:{inst_id}" in job_ids
```
with:
```python
    assert f"not_taken:{inst_id}" in job_ids
    assert f"remind1:{inst_id}" not in job_ids and f"remind2:{inst_id}" not in job_ids
    assert f"overdue:{inst_id}" not in job_ids
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_scheduler_recovery.py::test_recover_registers_all_jobs -v`
Expected: FAIL — `not_taken:{inst_id}` not in job_ids (old code still registers `remind1`/`remind2`/`overdue`)

- [ ] **Step 3: Modify `bot/services/scheduler_recovery_service.py`**

Replace the import line:
```python
    from bot.services.reminder_service import overdue_job, reminder_job
```
with:
```python
    from bot.services.reminder_service import not_taken_reminder_job
```

Replace the counters dict initialization:
```python
    counters = {"configs": 0, "reminders": 0, "overdue": 0,
                "auto_approve": 0, "questions": 0, "deliveries": 0}
```
with:
```python
    counters = {"configs": 0, "reminders": 0,
                "auto_approve": 0, "questions": 0, "deliveries": 0}
```

Replace section "2) напоминания и overdue открытых задач":
```python
        # 2) напоминания и overdue открытых задач
        for inst in await repo.get_open_with_reminders():
            base = inst.scheduled_at
            for n, hours in ((1, inst.remind_after_hours_snapshot),
                             (2, inst.second_remind_after_hours_snapshot)):
                if hours:
                    _add_job(scheduler, reminder_job,
                            _not_past(base + timedelta(hours=hours)),
                            [inst.id, n, bot, session_factory, scheduler],
                            f"remind{n}:{inst.id}")
                    counters["reminders"] += 1
            _add_job(scheduler, overdue_job, _not_past(base + timedelta(hours=24)),
                    [inst.id, bot, session_factory], f"overdue:{inst.id}")
            counters["overdue"] += 1
```
with:
```python
        # 2) единственное напоминание "не взято в работу" открытых задач
        not_taken_hours = int(await settings.get("reminders.not_taken_after_hours"))
        for inst in await repo.get_open_with_reminders():
            base = inst.scheduled_at
            _add_job(scheduler, not_taken_reminder_job,
                    _not_past(base + timedelta(hours=not_taken_hours)),
                    [inst.id, bot, session_factory], f"not_taken:{inst.id}")
            counters["reminders"] += 1
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_scheduler_recovery.py -v`
Expected: all pass (5 tests)

- [ ] **Step 5: Commit**

```bash
git add bot/services/scheduler_recovery_service.py tests/test_scheduler_recovery.py
git commit -m "feat: recover_jobs registers single not_taken job instead of remind1/remind2/overdue"
```

---

### Task 5: Full regression sweep

**Files:** none created/modified beyond verification.

- [ ] **Step 1: Search for any remaining reference to the deleted functions or job ids**

Run: `grep -rn "reminder_job\|overdue_job\|remind1\|remind2" bot/ tests/ --include=*.py`
Expected: zero matches outside of historical plan/spec docs under `docs/` (those are not code and are left as-is — they're a record of what was designed and decided, not something this plan retroactively edits).

If any match is found in `bot/` or `tests/*.py` outside what this plan already changed, investigate and fix before proceeding — it means a caller was missed.

- [ ] **Step 2: Run the full project test suite**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: all tests pass (no regressions), matching or exceeding the pre-plan baseline count (net test count changes: Task 1 unchanged count in that file, Task 2's file goes from 5 old tests to 6 new tests, Task 3's file stays the same count, Task 4's file stays the same count).

- [ ] **Step 3: Manually sanity-check the recovery counters change**

Run: `grep -rn 'counters\["overdue"\]\|counters\[.overdue.\]' bot/`
Expected: zero matches — confirms nothing else in the codebase reads the now-removed `"overdue"` key from the `recover_jobs` counters dict (the audit-log call site just stores the whole dict as JSON, no specific key access).

- [ ] **Step 4: No commit for this task** — verification only. If Step 1 or Step 3 finds a stray reference, fix it as part of whichever earlier task's file it belongs to and amend that task's commit is NOT allowed per project convention — instead make a small follow-up commit:
```bash
git add -A
git commit -m "fix: remove stray reference to deleted reminder mechanism found in regression sweep"
```
(Only run this if Step 1 or Step 3 actually found something — do not create an empty commit.)
