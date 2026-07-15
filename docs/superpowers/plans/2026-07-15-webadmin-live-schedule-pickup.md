# Live-подхват изменений расписания из веб-админки Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Tasks created/edited via `webadmin/routers/tasks.py` start running in the already-running bot's live APScheduler within 30 seconds, without a bot restart or dependency on Google Sheets sync.

**Architecture:** New `TaskConfig.pending_rebuild` flag, set by webadmin on save instead of webadmin computing `next_run_at` itself. A new always-on 30-second interval job in the bot reads configs with the flag set and calls the already-existing `rebuild_config_job` (which now also clears the flag) — the same function Google Sheets sync already uses, so there's exactly one place that recomputes `next_run_at` and registers the live APScheduler job.

**Tech Stack:** Existing stack (SQLAlchemy async, Alembic, APScheduler, pytest + SQLite in-memory `session_factory` fixture, httpx for webadmin tests). No new dependencies.

## Global Constraints

- Users/Topics/Articles CRUD in `webadmin/` are NOT touched — they have no live APScheduler state to pick up.
- No `enabled`/`interval_minutes` setting for this job — it is always registered, interval fixed at 30 seconds (explicit user decision, not configurable via `/admin`).
- Every DB-touching test reuses the existing `session_factory` fixture (SQLite in-memory).

---

### Task 1: `TaskConfig.pending_rebuild` column + repository method

**Files:**
- Modify: `bot/database/models.py`
- Create: `bot/database/migrations/versions/0008_pending_rebuild.py`
- Modify: `bot/database/repositories/task_repository.py`
- Test: `tests/test_task_repository.py`

**Interfaces:**
- Produces: `TaskConfig.pending_rebuild: bool` (default `False`); `TaskRepository.get_pending_rebuild() -> list[TaskConfig]` — consumed by Task 2's `pending_rebuild_job`.

- [ ] **Step 1: Modify `bot/database/models.py`** — add the column to `TaskConfig`

Add right after the `is_active` line (currently `bot/database/models.py:180`):
```python
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # Часть Ж (live-подхват из веб-админки): веб-форма выставляет True при
    # сохранении вместо того, чтобы самой считать next_run_at — единственный
    # источник истины по пересчёту расписания остаётся в rebuild_config_job
    # (тот же, что уже вызывает Sheets-синк). Сбрасывается rebuild_config_job'ом.
    pending_rebuild: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false")
```

- [ ] **Step 2: Create `bot/database/migrations/versions/0008_pending_rebuild.py`**

```python
"""add pending_rebuild to tasks_config

Revision ID: 0008
Revises: 0007
Create Date: 2026-07-15 21:00:00.000000

"""
import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"


def upgrade() -> None:
    op.add_column(
        "tasks_config",
        sa.Column("pending_rebuild", sa.Boolean(), nullable=False,
                 server_default=sa.false()))


def downgrade() -> None:
    op.drop_column("tasks_config", "pending_rebuild")
```

- [ ] **Step 3: Write the failing test for the repository method**

`tests/test_task_repository.py` already exists (uses the `session` fixture directly,
not `session_factory` — match this file's established convention, do not introduce
`session_factory` here). Add to the end of the file:
```python
async def test_get_pending_rebuild_returns_only_flagged_configs(session):
    repo = TaskRepository(session)
    flagged = await repo.upsert_config(dict(
        external_task_id="flagged", title="Flagged", schedule_type="daily",
        is_active=True))
    flagged.pending_rebuild = True
    not_flagged = await repo.upsert_config(dict(
        external_task_id="not_flagged", title="Not flagged", schedule_type="daily",
        is_active=True))
    await session.commit()

    result = await repo.get_pending_rebuild()
    result_ids = {c.id for c in result}
    assert flagged.id in result_ids
    assert not_flagged.id not in result_ids
```

- [ ] **Step 4: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_task_repository.py::test_get_pending_rebuild_returns_only_flagged_configs -v`
Expected: FAIL with `AttributeError: 'TaskRepository' object has no attribute 'get_pending_rebuild'`

- [ ] **Step 5: Modify `bot/database/repositories/task_repository.py`** — add the method

Add it near the other `get_*_configs` methods (e.g. right after `get_active_configs`):
```python
    async def get_pending_rebuild(self) -> list[TaskConfig]:
        return list(await self.session.scalars(
            select(TaskConfig).where(TaskConfig.pending_rebuild.is_(True))))
```

- [ ] **Step 6: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_task_repository.py -v`
Expected: `7 passed` (6 pre-existing + the new one — confirm none of the existing 6 regress)

- [ ] **Step 7: Commit**

```bash
git add bot/database/models.py bot/database/migrations/versions/0008_pending_rebuild.py bot/database/repositories/task_repository.py tests/test_task_repository.py
git commit -m "feat: TaskConfig.pending_rebuild flag + TaskRepository.get_pending_rebuild"
```

---

### Task 2: `pending_rebuild_job` + always-on registration

**Files:**
- Modify: `bot/services/scheduler_service.py`
- Test: `tests/test_scheduler_service.py`

**Interfaces:**
- Consumes: `TaskRepository.get_pending_rebuild` (Task 1).
- Produces: `pending_rebuild_job(bot, session_factory, scheduler_svc) -> None`; `SchedulerService.register_pending_rebuild_job()` — registered unconditionally in `setup_scheduler`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_scheduler_service.py`:
```python
async def test_rebuild_config_job_clears_pending_rebuild(session_factory):
    async with session_factory() as s:
        user = await UserRepository(s).upsert(telegram_id=9, name="Валя",
                                              role=Role.MANAGER_WB)
        repo = TaskRepository(s)
        cfg = await repo.upsert_config(dict(
            external_task_id="clears_pending", title="Проверка",
            scenario="simple", schedule_type="daily",
            responsible_user_id=user.id, is_active=True))
        cfg.pending_rebuild = True
        await s.commit()
        config_id = cfg.id

    scheduler = AsyncIOScheduler(timezone="UTC")
    svc = SchedulerService(scheduler, AsyncMock(), session_factory)
    await svc.rebuild_config_job(config_id)

    async with session_factory() as s:
        cfg = await TaskRepository(s).get_config(config_id)
        assert cfg.pending_rebuild is False


async def test_pending_rebuild_job_rebuilds_flagged_configs(session_factory):
    from bot.services.scheduler_service import pending_rebuild_job

    async with session_factory() as s:
        user = await UserRepository(s).upsert(telegram_id=11, name="Валя",
                                              role=Role.MANAGER_WB)
        repo = TaskRepository(s)
        cfg = await repo.upsert_config(dict(
            external_task_id="job_picks_me_up", title="Подхватить",
            scenario="simple", schedule_type="daily",
            responsible_user_id=user.id, is_active=True))
        cfg.pending_rebuild = True
        await s.commit()
        config_id = cfg.id

    scheduler = AsyncIOScheduler(timezone="UTC")
    svc = SchedulerService(scheduler, AsyncMock(), session_factory)
    await pending_rebuild_job(AsyncMock(), session_factory, svc)

    assert scheduler.get_job(f"config:{config_id}") is not None
    async with session_factory() as s:
        cfg = await TaskRepository(s).get_config(config_id)
        assert cfg.pending_rebuild is False


async def test_pending_rebuild_job_does_nothing_when_none_flagged(session_factory):
    from bot.services.scheduler_service import pending_rebuild_job

    scheduler = AsyncIOScheduler(timezone="UTC")
    svc = SchedulerService(scheduler, AsyncMock(), session_factory)
    await pending_rebuild_job(AsyncMock(), session_factory, svc)   # не должен упасть
    assert scheduler.get_jobs() == []


async def test_setup_scheduler_registers_pending_rebuild_job(session_factory):
    from bot.services.scheduler_service import setup_scheduler

    scheduler = await setup_scheduler(AsyncMock(), session_factory)
    assert scheduler.get_job("pending_rebuild") is not None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_scheduler_service.py -k pending_rebuild -v`
Expected: FAIL — `test_rebuild_config_job_clears_pending_rebuild` fails on the final assertion (flag not cleared), the other three fail with `ImportError`/`AssertionError` (function/job don't exist yet)

- [ ] **Step 3: Modify `bot/services/scheduler_service.py`** — clear the flag in `rebuild_config_job`

Replace:
```python
    async def rebuild_config_job(self, config_id: int) -> None:
        """Вызывается админ-панелью после изменения расписания."""
        from bot.database.repositories.task_repository import TaskRepository
        async with self.session_factory() as session:
            config = await TaskRepository(session).get_config(config_id)
            if config is None:
                return
            base = datetime.utcnow()
            config.next_run_at = (compute_next_run(config, base, after_change=True)
                                  if config.is_active else None)
            await session.commit()
            job_id = f"config:{config_id}"
            if self.scheduler.get_job(job_id):
                self.scheduler.remove_job(job_id)
            self.register_config_job(config)
```
with:
```python
    async def rebuild_config_job(self, config_id: int) -> None:
        """Вызывается админ-панелью, Sheets-синком и pending_rebuild_job'ом
        (Часть Ж) после изменения расписания — единственное место, где
        реально пересчитывается next_run_at и переустанавливается живой
        APScheduler-джоб. Сбрасывает pending_rebuild безусловно: даже если
        вызывающий код (Sheets-синк) не выставлял этот флаг, сброс уже-False
        значения — no-op."""
        from bot.database.repositories.task_repository import TaskRepository
        async with self.session_factory() as session:
            config = await TaskRepository(session).get_config(config_id)
            if config is None:
                return
            base = datetime.utcnow()
            config.next_run_at = (compute_next_run(config, base, after_change=True)
                                  if config.is_active else None)
            config.pending_rebuild = False
            await session.commit()
            job_id = f"config:{config_id}"
            if self.scheduler.get_job(job_id):
                self.scheduler.remove_job(job_id)
            self.register_config_job(config)
```

- [ ] **Step 4: Add `pending_rebuild_job` and `register_pending_rebuild_job`**

Add the module-level function `pending_rebuild_job` right after `compute_next_run` (near the top of the file, alongside the other module-level job functions' natural location — actually this file currently only has `compute_next_run` as a bare function; place `pending_rebuild_job` directly above the `class SchedulerService:` line):
```python
async def pending_rebuild_job(bot, session_factory, scheduler_svc) -> None:
    """Каждые 30 секунд подхватывает задачи, изменённые через веб-админку
    (Часть Ж): веб-форма выставляет TaskConfig.pending_rebuild=True вместо
    того, чтобы самой считать next_run_at. rebuild_config_job — тот же,
    что уже используют Sheets-синк и Telegram /admin — делает пересчёт и
    сам сбрасывает флаг."""
    from bot.database.repositories.task_repository import TaskRepository
    async with session_factory() as session:
        config_ids = [cfg.id for cfg in await TaskRepository(session).get_pending_rebuild()]
    for config_id in config_ids:
        await scheduler_svc.rebuild_config_job(config_id)
```

Add the registration method to `SchedulerService`, right after `register_sync_job` (or anywhere alongside the other `register_*_job` methods):
```python
    async def register_pending_rebuild_job(self) -> None:
        """Всегда включён — не опциональная фича вроде Sheets-синка, а
        базовая корректность живого подхвата изменений из веб-админки."""
        if self.scheduler.get_job("pending_rebuild"):
            self.scheduler.remove_job("pending_rebuild")
        self.scheduler.add_job(pending_rebuild_job, "interval", seconds=30,
                               args=[self.bot, self.session_factory, self],
                               id="pending_rebuild", misfire_grace_time=GRACE)
```

- [ ] **Step 5: Wire into `setup_scheduler`**

Add after `await svc.register_status_history_job()`:
```python
    await svc.register_pending_rebuild_job()
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_scheduler_service.py -v`
Expected: all pass (previous tests + 4 new ones)

- [ ] **Step 7: Commit**

```bash
git add bot/services/scheduler_service.py tests/test_scheduler_service.py
git commit -m "feat: pending_rebuild_job (always-on, 30s) picks up webadmin schedule changes"
```

---

### Task 3: Webadmin sets `pending_rebuild` instead of computing `next_run_at`

**Files:**
- Modify: `webadmin/routers/tasks.py`
- Modify: `tests/webadmin/test_tasks_crud.py`

**Interfaces:**
- Consumes: `TaskConfig.pending_rebuild` (Task 1).

- [ ] **Step 1: Modify `webadmin/routers/tasks.py`** — `task_create`

Replace:
```python
    cfg = await TaskRepository(session).upsert_config(payload)
    if cfg.is_active:
        cfg.next_run_at = compute_next_run(cfg, datetime.utcnow(), after_change=True)
    await session.commit()
    return RedirectResponse(url="/tasks", status_code=303)


@router.get("/tasks/{config_id}/edit", response_class=HTMLResponse)
```
with:
```python
    cfg = await TaskRepository(session).upsert_config(payload)
    cfg.pending_rebuild = True
    await session.commit()
    return RedirectResponse(url="/tasks", status_code=303)


@router.get("/tasks/{config_id}/edit", response_class=HTMLResponse)
```

- [ ] **Step 2: Modify `webadmin/routers/tasks.py`** — `task_update`

Replace:
```python
    cfg = await repo.upsert_config(payload)
    cfg.next_run_at = (compute_next_run(cfg, datetime.utcnow(), after_change=True)
                      if cfg.is_active else None)
    await session.commit()
    return RedirectResponse(url="/tasks", status_code=303)
```
with:
```python
    cfg = await repo.upsert_config(payload)
    cfg.pending_rebuild = True
    await session.commit()
    return RedirectResponse(url="/tasks", status_code=303)
```

- [ ] **Step 3: Remove the now-unused imports**

Remove two lines from the top of `webadmin/routers/tasks.py`, both now unused after Steps 1-2 (verify with `grep -n "datetime\|compute_next_run" webadmin/routers/tasks.py` before removing — each should show zero remaining usages besides the import line itself):
```python
from datetime import datetime
```
and
```python
from bot.services.scheduler_service import compute_next_run
```

- [ ] **Step 4: Update the now-stale webadmin test**

Replace (in `tests/webadmin/test_tasks_crud.py`):
```python
async def test_create_task_sets_next_run_at_for_today_when_active(client, session_factory):
    token = await login_staff(client)
    resp = await client.post("/tasks/new", data={
        "title": "Активная задача", "scenario": "simple", "responsible_user_id": "",
        "responsible_role": "", "topic_id": "", "schedule_type": "daily",
        "schedule_value": "", "schedule_interval": "", "time": "23:59",
        "due_time": "", "due_days_offset": "0", "is_active": "on", "csrf_token": token})
    assert resp.status_code == 303
    from sqlalchemy import select
    from bot.database.models import TaskConfig
    async with session_factory() as session:
        cfg = await session.scalar(select(TaskConfig).where(TaskConfig.title == "Активная задача"))
        assert cfg.next_run_at is not None
```
with:
```python
async def test_create_task_sets_pending_rebuild_when_active(client, session_factory):
    """Веб-форма больше не считает next_run_at сама — только выставляет
    pending_rebuild, единственный пересчёт делает bot's rebuild_config_job
    (см. docs/superpowers/plans/2026-07-15-webadmin-live-schedule-pickup.md)."""
    token = await login_staff(client)
    resp = await client.post("/tasks/new", data={
        "title": "Активная задача", "scenario": "simple", "responsible_user_id": "",
        "responsible_role": "", "topic_id": "", "schedule_type": "daily",
        "schedule_value": "", "schedule_interval": "", "time": "23:59",
        "due_time": "", "due_days_offset": "0", "is_active": "on", "csrf_token": token})
    assert resp.status_code == 303
    from sqlalchemy import select
    from bot.database.models import TaskConfig
    async with session_factory() as session:
        cfg = await session.scalar(select(TaskConfig).where(TaskConfig.title == "Активная задача"))
        assert cfg.pending_rebuild is True
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/webadmin/test_tasks_crud.py -v`
Expected: `4 passed`

- [ ] **Step 6: Run the full project test suite**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: all pass, no regressions.

- [ ] **Step 7: Commit**

```bash
git add webadmin/routers/tasks.py tests/webadmin/test_tasks_crud.py
git commit -m "feat(webadmin): task create/edit set pending_rebuild instead of computing next_run_at directly"
```
