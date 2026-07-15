# Веб-админка (FastAPI) вместо Google Sheets + инфографика расписания — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a new `webadmin/` FastAPI package (server-rendered Jinja2 + Tailwind CDN) that provides, alongside the existing Telegram bot and Google Sheets sync (both untouched): a staff-only schedule calendar (week/month), delivery-log and status-history report pages, a client-facing simplified schedule page, and CRUD screens for Tasks/Users/Topics/Articles — all reading/writing the same Postgres tables via existing repositories.

**Architecture:** One new Python package `webadmin/` reusing `bot/database/db.py`'s `create_engine_and_factory` and the existing repositories (`TaskRepository`, `UserRepository`, `TopicRepository`, `ArticleRepository`). Two independent cookie-session auth gates (staff password, client password) via `starlette.middleware.sessions.SessionMiddleware`. Local-only for this plan — no Docker/deploy changes.

**Tech Stack:** FastAPI, Jinja2 (server-rendered templates), Tailwind via CDN, `starlette` SessionMiddleware, `httpx.AsyncClient` + `ASGITransport` for tests (same `session_factory` SQLite fixture already in `tests/conftest.py`).

## Global Constraints

- Google Sheets sync (`sync.auto_enabled`) and log jobs (`delivery_log.enabled`, `status_history_log.enabled`) are NOT touched, disabled, or removed by this plan.
- Telegram `/admin` (`bot/handlers/admin/*.py`) is NOT modified except for two small, additive repository method extensions noted in Tasks 9 and 11 (backward-compatible, existing callers unaffected).
- No hard deletes anywhere — CRUD only toggles `is_active` (matches the project's existing Global Constraint, already followed by Sheets sync and Telegram `/admin`).
- No deploy/docker-compose changes — local `uvicorn` run only. Deployment is an explicit separate step the user will request later.
- Templates load Tailwind via `<script src="https://cdn.tailwindcss.com">` without a Subresource Integrity hash. This is intentional, not an oversight: that URL serves a JIT compiler that regenerates its own content per page and has no fixed, hashable version to pin — Tailwind's own docs mark this CDN build as prototyping-only for exactly this reason. Acceptable for this plan's local-only scope; before any real deploy, replace it with a locally-built/pinned Tailwind CSS file (own follow-up task, not part of this plan).
- Every new DB-touching test reuses the existing `session_factory` fixture (SQLite in-memory) from `tests/conftest.py` — no new fixture files duplicate that setup.

---

### Task 1: Package scaffolding, config, DB session, health check

**Files:**
- Modify: `requirements.txt`
- Modify: `requirements-dev.txt`
- Modify: `.env.example`
- Create: `webadmin/__init__.py`
- Create: `webadmin/config.py`
- Create: `webadmin/deps.py`
- Create: `webadmin/main.py`
- Create: `tests/webadmin/__init__.py`
- Create: `tests/webadmin/conftest.py`
- Test: `tests/webadmin/test_app.py`

**Interfaces:**
- Produces: `webadmin.config.get_webadmin_settings() -> WebAdminSettings` (fields: `database_url`, `webadmin_password`, `client_password`, `webadmin_secret_key`); `webadmin.deps.get_db` (FastAPI dependency yielding `AsyncSession`); `webadmin.main.create_app() -> FastAPI`; `webadmin.main.app` (module-level instance); `tests/webadmin/conftest.py` fixtures `app`, `client` (both consumed by every later test file in this plan).

- [ ] **Step 1: Add new dependencies**

Append to `requirements.txt`:
```
fastapi==0.115.0
uvicorn[standard]==0.32.0
jinja2==3.1.4
python-multipart==0.0.12
itsdangerous==2.2.0
```

Append to `requirements-dev.txt`:
```
httpx==0.27.2
```

- [ ] **Step 2: Install dependencies**

Run: `.venv/Scripts/pip install -r requirements-dev.txt` (Linux/macOS: `.venv/bin/pip`)
Expected: all packages install without error.

- [ ] **Step 3: Add webadmin env vars to `.env.example`**

Append to `.env.example`:
```
# Веб-админка (webadmin/) — локальный запуск, см. docs/superpowers/specs/2026-07-15-web-admin-dashboard-design.md
WEBADMIN_PASSWORD=REPLACE_ME
CLIENT_PASSWORD=REPLACE_ME
WEBADMIN_SECRET_KEY=REPLACE_ME_RANDOM_STRING
```

Also add the same three lines (with real values, not `REPLACE_ME`) to your actual local `.env` — `WebAdminSettings` below requires them present with no default.

- [ ] **Step 4: Create `webadmin/__init__.py`**

```python
```
(empty file — marks `webadmin` as a package)

- [ ] **Step 5: Create `webadmin/config.py`**

```python
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class WebAdminSettings(BaseSettings):
    """Секреты и инфраструктура веб-админки — по аналогии с bot/config.py."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str
    webadmin_password: str
    client_password: str
    webadmin_secret_key: str


@lru_cache
def get_webadmin_settings() -> WebAdminSettings:
    return WebAdminSettings()
```

- [ ] **Step 6: Create `webadmin/deps.py`**

```python
from functools import lru_cache

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.database.db import create_engine_and_factory
from webadmin.config import get_webadmin_settings


@lru_cache
def get_session_factory() -> async_sessionmaker[AsyncSession]:
    settings = get_webadmin_settings()
    _, factory = create_engine_and_factory(settings.database_url)
    return factory


async def get_db():
    factory = get_session_factory()
    async with factory() as session:
        yield session
```

- [ ] **Step 7: Create `webadmin/main.py`**

```python
from fastapi import FastAPI
from fastapi.responses import PlainTextResponse


def create_app() -> FastAPI:
    app = FastAPI(title="WB Task Bot — веб-админка")

    @app.get("/healthz", response_class=PlainTextResponse)
    async def healthz() -> str:
        return "ok"

    return app


app = create_app()
```

- [ ] **Step 8: Create `tests/webadmin/__init__.py`**

```python
```
(empty file)

- [ ] **Step 9: Create `tests/webadmin/conftest.py`**

```python
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from webadmin.config import get_webadmin_settings


@pytest.fixture(autouse=True)
def _webadmin_env(monkeypatch):
    """WebAdminSettings требует все поля без дефолта — подставляем тестовые
    значения через env, не трогая реальный .env разработчика."""
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    monkeypatch.setenv("WEBADMIN_PASSWORD", "test-staff-pw")
    monkeypatch.setenv("CLIENT_PASSWORD", "test-client-pw")
    monkeypatch.setenv("WEBADMIN_SECRET_KEY", "test-secret-key")
    get_webadmin_settings.cache_clear()
    yield
    get_webadmin_settings.cache_clear()


@pytest_asyncio.fixture
async def app(session_factory):
    from webadmin.deps import get_db
    from webadmin.main import create_app

    application = create_app()

    async def _override_get_db():
        async with session_factory() as session:
            yield session

    application.dependency_overrides[get_db] = _override_get_db
    return application


@pytest_asyncio.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
```

- [ ] **Step 10: Write the failing test**

Create `tests/webadmin/test_app.py`:
```python
async def test_healthz_returns_ok(client):
    resp = await client.get("/healthz")
    assert resp.status_code == 200
    assert resp.text == "ok"
```

- [ ] **Step 11: Run test to verify it passes (scaffolding has no reason to fail, but confirm the harness works)**

Run: `pytest tests/webadmin/test_app.py -v`
Expected: `1 passed`

- [ ] **Step 12: Manual local smoke check**

Run: `uvicorn webadmin.main:app --reload --port 8080`
Then in another terminal: `curl http://localhost:8080/healthz`
Expected: `ok`

- [ ] **Step 13: Commit**

```bash
git add requirements.txt requirements-dev.txt .env.example webadmin tests/webadmin
git commit -m "feat(webadmin): scaffold FastAPI app with health check"
```

---

### Task 2: Staff login/logout

**Files:**
- Create: `webadmin/auth.py`
- Create: `webadmin/routers/__init__.py`
- Create: `webadmin/routers/auth.py`
- Create: `webadmin/templates/login.html`
- Modify: `webadmin/main.py`
- Test: `tests/webadmin/test_auth.py`

**Interfaces:**
- Consumes: `webadmin.config.get_webadmin_settings` (Task 1).
- Produces: `webadmin.auth.STAFF_SESSION_KEY`, `check_staff_password(password: str) -> bool`, `is_staff(request) -> bool`, `require_staff` (FastAPI dependency), `StaffLoginRequired` (exception) — all consumed by every staff-protected router in Tasks 5, 7–12.

- [ ] **Step 1: Create `webadmin/auth.py`**

```python
from fastapi import Request

from webadmin.config import get_webadmin_settings

STAFF_SESSION_KEY = "staff_authenticated"


class StaffLoginRequired(Exception):
    pass


def check_staff_password(password: str) -> bool:
    return password == get_webadmin_settings().webadmin_password


def is_staff(request: Request) -> bool:
    return bool(request.session.get(STAFF_SESSION_KEY))


async def require_staff(request: Request) -> None:
    if not is_staff(request):
        raise StaffLoginRequired()
```

- [ ] **Step 2: Create `webadmin/routers/__init__.py`**

```python
```
(empty file)

- [ ] **Step 3: Create `webadmin/templates/login.html`**

```html
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <title>Вход — WB Task Bot</title>
    <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-gray-50 h-screen flex items-center justify-center">
    <form method="post" action="/login" class="bg-white p-8 rounded-lg shadow-md w-80 space-y-4">
        <h1 class="text-xl font-semibold">Вход в веб-админку</h1>
        {% if error %}<p class="text-red-600 text-sm">{{ error }}</p>{% endif %}
        <input type="password" name="password" placeholder="Пароль" required
               class="w-full border rounded px-3 py-2">
        <button type="submit" class="w-full bg-slate-800 text-white rounded px-3 py-2">Войти</button>
    </form>
</body>
</html>
```

- [ ] **Step 4: Create `webadmin/routers/auth.py`**

```python
from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from webadmin.auth import STAFF_SESSION_KEY, check_staff_password

router = APIRouter()
templates = Jinja2Templates(directory="webadmin/templates")


@router.get("/login", response_class=HTMLResponse)
async def login_form(request: Request):
    return templates.TemplateResponse("login.html", {"request": request, "error": None})


@router.post("/login", response_class=HTMLResponse)
async def login_submit(request: Request, password: str = Form(...)):
    if not check_staff_password(password):
        return templates.TemplateResponse(
            "login.html", {"request": request, "error": "Неверный пароль"}, status_code=401)
    request.session[STAFF_SESSION_KEY] = True
    return RedirectResponse(url="/", status_code=303)


@router.post("/logout")
async def logout(request: Request):
    request.session.pop(STAFF_SESSION_KEY, None)
    return RedirectResponse(url="/login", status_code=303)
```

- [ ] **Step 5: Modify `webadmin/main.py`** — add session middleware, staff-login exception handler, a protected placeholder home route, and include the auth router

Replace the whole file:
```python
from fastapi import Depends, FastAPI, Request
from fastapi.responses import PlainTextResponse, RedirectResponse
from starlette.middleware.sessions import SessionMiddleware

from webadmin.auth import StaffLoginRequired, require_staff
from webadmin.config import get_webadmin_settings
from webadmin.routers.auth import router as auth_router


def create_app() -> FastAPI:
    app = FastAPI(title="WB Task Bot — веб-админка")
    app.add_middleware(SessionMiddleware, secret_key=get_webadmin_settings().webadmin_secret_key)

    @app.exception_handler(StaffLoginRequired)
    async def _staff_login_required(request: Request, exc: StaffLoginRequired):
        return RedirectResponse(url="/login", status_code=303)

    @app.get("/healthz", response_class=PlainTextResponse)
    async def healthz() -> str:
        return "ok"

    @app.get("/", response_class=PlainTextResponse, dependencies=[Depends(require_staff)])
    async def home() -> str:
        return "ok, staff"

    app.include_router(auth_router)
    return app


app = create_app()
```

- [ ] **Step 6: Write the failing tests**

Create `tests/webadmin/test_auth.py`:
```python
async def test_home_redirects_to_login_when_not_authenticated(client):
    resp = await client.get("/")
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"


async def test_login_wrong_password_shows_error(client):
    resp = await client.post("/login", data={"password": "wrong"})
    assert resp.status_code == 401
    assert "Неверный пароль" in resp.text


async def test_login_correct_password_grants_access(client):
    resp = await client.post("/login", data={"password": "test-staff-pw"})
    assert resp.status_code == 303
    assert resp.headers["location"] == "/"
    home = await client.get("/")
    assert home.status_code == 200
    assert home.text == "ok, staff"


async def test_logout_revokes_access(client):
    await client.post("/login", data={"password": "test-staff-pw"})
    await client.post("/logout")
    resp = await client.get("/")
    assert resp.status_code == 303
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `pytest tests/webadmin/test_auth.py -v`
Expected: `4 passed`

- [ ] **Step 8: Commit**

```bash
git add webadmin tests/webadmin/test_auth.py
git commit -m "feat(webadmin): staff login/logout with cookie session"
```

---

### Task 3: Client login (separate gate)

> **Retrofit note (post-Task-2 security fix):** Task 2's review found and fixed a timing-attack password comparison and added CSRF protection (`webadmin/csrf.py`: `get_or_create_csrf_token`, `verify_csrf_token`). This task mirrors both fixes from the start — `check_client_password` uses `hmac.compare_digest` directly (not `==`), and `client_login.html`/`client_login_submit` carry the same CSRF pattern as `login.html`/`login_submit`. Read `webadmin/auth.py` and `webadmin/csrf.py` as they exist after Task 2 before starting.

**Files:**
- Modify: `webadmin/auth.py`
- Modify: `webadmin/routers/auth.py`
- Create: `webadmin/templates/client_login.html`
- Modify: `webadmin/main.py`
- Modify: `tests/webadmin/test_auth.py`

**Interfaces:**
- Consumes: `webadmin.csrf.{get_or_create_csrf_token, verify_csrf_token}` (Task 2).
- Produces: `webadmin.auth.CLIENT_SESSION_KEY`, `check_client_password`, `is_client`, `require_client`, `ClientLoginRequired` — consumed by Task 6's `/client/schedule` route.

- [ ] **Step 1: Modify `webadmin/auth.py`** — add client-side equivalents, hmac-safe from the start

Add `import hmac` at the top if it isn't already there (Task 2 added it for `check_staff_password` — reuse the same import, don't duplicate it).

Add to the end of `webadmin/auth.py`:
```python
CLIENT_SESSION_KEY = "client_authenticated"


class ClientLoginRequired(Exception):
    pass


def check_client_password(password: str) -> bool:
    return hmac.compare_digest(password, get_webadmin_settings().client_password)


def is_client(request: Request) -> bool:
    return bool(request.session.get(CLIENT_SESSION_KEY))


async def require_client(request: Request) -> None:
    if not is_client(request):
        raise ClientLoginRequired()
```

- [ ] **Step 2: Create `webadmin/templates/client_login.html`**

```html
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <title>Расписание — вход</title>
    <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-gray-50 h-screen flex items-center justify-center">
    <form method="post" action="/client/login" class="bg-white p-8 rounded-lg shadow-md w-80 space-y-4">
        <h1 class="text-xl font-semibold">Расписание задач</h1>
        {% if error %}<p class="text-red-600 text-sm">{{ error }}</p>{% endif %}
        <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
        <input type="password" name="password" placeholder="Пароль" required
               class="w-full border rounded px-3 py-2">
        <button type="submit" class="w-full bg-slate-800 text-white rounded px-3 py-2">Войти</button>
    </form>
</body>
</html>
```

- [ ] **Step 3: Modify `webadmin/routers/auth.py`** — add client login routes with CSRF, same pattern as staff login

Replace the import line `from webadmin.auth import STAFF_SESSION_KEY, check_staff_password` with:
```python
from webadmin.auth import (
    CLIENT_SESSION_KEY, STAFF_SESSION_KEY, check_client_password, check_staff_password,
)
from webadmin.csrf import get_or_create_csrf_token, verify_csrf_token
```
(if `webadmin.csrf` is already imported in this file from Task 2's fix, don't duplicate the import — merge into the existing one.)

Add to the end of `webadmin/routers/auth.py`:
```python
@router.get("/client/login", response_class=HTMLResponse)
async def client_login_form(request: Request):
    token = get_or_create_csrf_token(request)
    return templates.TemplateResponse(
        "client_login.html", {"request": request, "error": None, "csrf_token": token})


@router.post("/client/login", response_class=HTMLResponse)
async def client_login_submit(request: Request, password: str = Form(...),
                              csrf_token: str = Form(...)):
    if not verify_csrf_token(request, csrf_token):
        return templates.TemplateResponse(
            "client_login.html", {"request": request, "error": "Сессия истекла, попробуйте войти ещё раз",
                                  "csrf_token": get_or_create_csrf_token(request)}, status_code=400)
    if not check_client_password(password):
        return templates.TemplateResponse(
            "client_login.html", {"request": request, "error": "Неверный пароль",
                                  "csrf_token": get_or_create_csrf_token(request)}, status_code=401)
    request.session[CLIENT_SESSION_KEY] = True
    return RedirectResponse(url="/client", status_code=303)
```

- [ ] **Step 4: Modify `webadmin/main.py`** — client exception handler + placeholder `/client` route (Task 6 will turn this into a redirect to the real schedule page)

Replace the import line `from webadmin.auth import StaffLoginRequired, require_staff` with:
```python
from webadmin.auth import ClientLoginRequired, StaffLoginRequired, require_client, require_staff
```

Add inside `create_app()`, after the staff exception handler:
```python
    @app.exception_handler(ClientLoginRequired)
    async def _client_login_required(request: Request, exc: ClientLoginRequired):
        return RedirectResponse(url="/client/login", status_code=303)
```

Add a new route, after the `home` route:
```python
    @app.get("/client", response_class=PlainTextResponse, dependencies=[Depends(require_client)])
    async def client_home() -> str:
        return "ok, client"
```

- [ ] **Step 5: Write the failing tests**

`tests/webadmin/test_auth.py` already has a module-level `_extract_csrf_token(html: str) -> str` helper (added by Task 2's security fix) — reuse it as-is, do not redefine it under a different name.

Add the tests:
```python
async def test_client_home_redirects_to_client_login(client):
    resp = await client.get("/client")
    assert resp.status_code == 303
    assert resp.headers["location"] == "/client/login"


async def test_client_login_wrong_password(client):
    form = await client.get("/client/login")
    token = _extract_csrf_token(form.text)
    resp = await client.post("/client/login", data={"password": "wrong", "csrf_token": token})
    assert resp.status_code == 401


async def test_client_login_without_csrf_token_rejected(client):
    resp = await client.post("/client/login", data={"password": "test-client-pw"})
    assert resp.status_code == 422


async def test_client_login_with_wrong_csrf_token_rejected(client):
    await client.get("/client/login")  # seeds a session + real token
    resp = await client.post("/client/login", data={
        "password": "test-client-pw", "csrf_token": "forged"})
    assert resp.status_code == 400
    assert "Сессия истекла" in resp.text


async def test_client_login_correct_password_grants_access(client):
    form = await client.get("/client/login")
    token = _extract_csrf_token(form.text)
    resp = await client.post("/client/login", data={"password": "test-client-pw", "csrf_token": token})
    assert resp.status_code == 303
    assert resp.headers["location"] == "/client"
    home = await client.get("/client")
    assert home.status_code == 200


async def test_client_session_does_not_grant_staff_access(client):
    form = await client.get("/client/login")
    token = _extract_csrf_token(form.text)
    await client.post("/client/login", data={"password": "test-client-pw", "csrf_token": token})
    resp = await client.get("/")
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/webadmin/test_auth.py -v`
Expected: `12 passed` (6 from Task 2 + 6 new client tests)

- [ ] **Step 7: Commit**

```bash
git add webadmin tests/webadmin/test_auth.py
git commit -m "feat(webadmin): client login gate with CSRF, separate from staff session"
```

---

### Task 4: Schedule projection (pure logic, no DB, no HTTP)

**Files:**
- Create: `webadmin/schedule_projection.py`
- Test: `tests/webadmin/test_schedule_projection.py`

**Interfaces:**
- Consumes: `bot.database.models.ScheduleType`, `bot.database.models.TaskConfig` (existing).
- Produces: `project_occurrences(config: TaskConfig, range_start: date, range_end: date) -> list[datetime]`, `week_range(ref: date) -> tuple[date, date]`, `month_range(ref: date) -> tuple[date, date]`, `shift_period(start: date, end: date, period: str, direction: int) -> tuple[date, date]` — all consumed by Task 5's `/schedule` route and Task 6's `/client/schedule` route.

- [ ] **Step 1: Write the failing tests**

Create `tests/webadmin/test_schedule_projection.py`:
```python
from datetime import date, datetime, time

from bot.database.models import TaskConfig
from webadmin.schedule_projection import (
    month_range, project_occurrences, shift_period, week_range,
)


def cfg(**kw):
    base = dict(external_task_id="x", title="x", schedule_type="daily",
                time=time(9, 0), run_on_weekends=True, is_active=True)
    base.update(kw)
    return TaskConfig(**base)


def test_daily_occurs_every_day_in_range():
    c = cfg(schedule_type="daily")
    result = project_occurrences(c, date(2026, 7, 13), date(2026, 7, 15))
    assert result == [
        datetime(2026, 7, 13, 9, 0), datetime(2026, 7, 14, 9, 0), datetime(2026, 7, 15, 9, 0),
    ]


def test_daily_skips_weekends_when_disabled():
    c = cfg(schedule_type="daily", run_on_weekends=False)
    result = project_occurrences(c, date(2026, 7, 10), date(2026, 7, 13))  # Пт-Пн
    assert [d.date() for d in result] == [date(2026, 7, 10), date(2026, 7, 13)]


def test_every_n_days_uses_next_run_at_as_phase():
    c = cfg(schedule_type="every_n_days", schedule_interval=2,
            next_run_at=datetime(2026, 7, 15, 9, 0))
    result = project_occurrences(c, date(2026, 7, 15), date(2026, 7, 21))
    assert [d.date() for d in result] == [
        date(2026, 7, 15), date(2026, 7, 17), date(2026, 7, 19), date(2026, 7, 21),
    ]


def test_weekly_multiple_days():
    c = cfg(schedule_type="weekly", schedule_value="0,2,4", time=time(10, 0))  # пн/ср/пт
    result = project_occurrences(c, date(2026, 7, 13), date(2026, 7, 19))  # пн-вс
    assert [d.date() for d in result] == [date(2026, 7, 13), date(2026, 7, 15), date(2026, 7, 17)]


def test_monthly_clamps_to_month_end():
    c = cfg(schedule_type="monthly", schedule_value="31")
    result = project_occurrences(c, date(2026, 2, 1), date(2026, 2, 28))
    assert [d.date() for d in result] == [date(2026, 2, 28)]


def test_cron_matches_expression():
    c = cfg(schedule_type="cron", schedule_value="30 14 * * *")
    result = project_occurrences(c, date(2026, 7, 15), date(2026, 7, 16))
    assert result == [datetime(2026, 7, 15, 14, 30), datetime(2026, 7, 16, 14, 30)]


def test_inactive_config_returns_nothing():
    c = cfg(schedule_type="daily", is_active=False)
    assert project_occurrences(c, date(2026, 7, 13), date(2026, 7, 15)) == []


def test_week_range_monday_to_sunday():
    assert week_range(date(2026, 7, 15)) == (date(2026, 7, 13), date(2026, 7, 19))  # 15 июля — среда


def test_month_range_first_to_last_day():
    assert month_range(date(2026, 2, 10)) == (date(2026, 2, 1), date(2026, 2, 28))


def test_shift_period_week_forward_and_back():
    start, end = date(2026, 7, 13), date(2026, 7, 19)
    assert shift_period(start, end, "week", 1) == (date(2026, 7, 20), date(2026, 7, 26))
    assert shift_period(start, end, "week", -1) == (date(2026, 7, 6), date(2026, 7, 12))


def test_shift_period_month_forward_and_back():
    start, end = date(2026, 7, 1), date(2026, 7, 31)
    assert shift_period(start, end, "month", 1) == (date(2026, 8, 1), date(2026, 8, 31))
    assert shift_period(start, end, "month", -1) == (date(2026, 6, 1), date(2026, 6, 30))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/webadmin/test_schedule_projection.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'webadmin.schedule_projection'`

- [ ] **Step 3: Create `webadmin/schedule_projection.py`**

```python
import calendar
from datetime import date, datetime, time, timedelta

from apscheduler.triggers.cron import CronTrigger

from bot.database.models import ScheduleType, TaskConfig


def date_range(start: date, end: date):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def project_occurrences(config: TaskConfig, range_start: date, range_end: date) -> list[datetime]:
    """Все моменты, когда TaskConfig сработал бы в [range_start, range_end]
    (обе даты включительно) — только для отображения графика, ничего не
    создаёт и не меняет в БД. Считает по типу расписания напрямую (не через
    scheduler_service.compute_next_run — та функция односторонняя, "вперёд
    от anchor", и не годится для произвольного диапазона, включая прошлое).
    Для EVERY_N_DAYS единственный способ узнать фазу цикла без реального
    anchor — взять config.next_run_at (уже корректно посчитан планировщиком)
    как точку отсчёта; если его нет — created_at."""
    if not config.is_active:
        return []
    st = config.schedule_type
    t = config.time or time(9, 0)
    if st == ScheduleType.CRON:
        return _project_cron(config, range_start, range_end)
    result: list[datetime] = []
    for d in date_range(range_start, range_end):
        if not config.run_on_weekends and d.weekday() >= 5:
            continue
        if _matches(config, st, d):
            result.append(datetime.combine(d, t))
    return result


def _matches(config: TaskConfig, st: str, d: date) -> bool:
    if st == ScheduleType.DAILY:
        return True
    if st == ScheduleType.EVERY_N_DAYS:
        interval = config.schedule_interval or 1
        anchor = (config.next_run_at.date() if config.next_run_at
                  else (config.created_at.date() if config.created_at else d))
        return (d - anchor).days % interval == 0
    if st == ScheduleType.WEEKLY:
        targets = [int(v) for v in str(config.schedule_value or "0").split(",") if v.strip()]
        return d.weekday() in targets
    if st == ScheduleType.MONTHLY:
        target_day = int(config.schedule_value or 1)
        return d.day == min(target_day, calendar.monthrange(d.year, d.month)[1])
    return False


def _project_cron(config: TaskConfig, range_start: date, range_end: date) -> list[datetime]:
    trigger = CronTrigger.from_crontab(config.schedule_value or "0 9 * * *")
    cursor = datetime.combine(range_start - timedelta(days=1), time(23, 59))
    result: list[datetime] = []
    for _ in range(400):  # предохранитель от зацикливания на битом выражении
        fire = trigger.get_next_fire_time(None, cursor)
        if fire is None or fire.date() > range_end:
            break
        result.append(fire.replace(tzinfo=None))
        cursor = fire
    return result


def week_range(ref: date) -> tuple[date, date]:
    start = ref - timedelta(days=ref.weekday())
    return start, start + timedelta(days=6)


def month_range(ref: date) -> tuple[date, date]:
    start = ref.replace(day=1)
    end = ref.replace(day=calendar.monthrange(ref.year, ref.month)[1])
    return start, end


def shift_period(start: date, end: date, period: str, direction: int) -> tuple[date, date]:
    if period == "week":
        delta = timedelta(days=7 * direction)
        return start + delta, end + delta
    anchor = (end + timedelta(days=1)) if direction > 0 else (start - timedelta(days=1))
    return month_range(anchor)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/webadmin/test_schedule_projection.py -v`
Expected: `12 passed`

- [ ] **Step 5: Commit**

```bash
git add webadmin/schedule_projection.py tests/webadmin/test_schedule_projection.py
git commit -m "feat(webadmin): pure schedule projection for calendar grid"
```

---

### Task 5: `/schedule` staff report page

> **Retrofit note (CSRF on the logout form):** Task 2's `POST /logout` already requires a `csrf_token` form field. `base.html` (created in this task) embeds the logout button as a real `<form>` for the first time, so it needs a way to render a valid token without every future GET route (this task's `/schedule`, and Tasks 7–12's list pages) having to remember to pass one into the template context. This task fixes that once: `require_staff` (already a dependency on every staff route) additionally stashes a fresh/existing token on `request.state.csrf_token`, and `base.html` reads it directly from `request` — no per-route wiring needed, now or later.

**Files:**
- Modify: `webadmin/auth.py` (`require_staff` sets `request.state.csrf_token`)
- Create: `webadmin/templates/base.html`
- Create: `webadmin/templates/schedule.html`
- Create: `webadmin/routers/reports.py`
- Modify: `webadmin/main.py` (include reports router)
- Create: `tests/webadmin/helpers.py`
- Test: `tests/webadmin/test_reports.py`

**Interfaces:**
- Consumes: `webadmin.schedule_projection.{project_occurrences, week_range, month_range, shift_period}` (Task 4), `webadmin.auth.require_staff` (Task 2, modified here), `webadmin.csrf.get_or_create_csrf_token` (Task 2), `webadmin.deps.get_db` (Task 1).
- Produces: `GET /schedule` route; `webadmin/templates/base.html` (extended by all later staff templates in Tasks 7–12); `tests/webadmin/helpers.py` (`login_staff`, `login_client`, `extract_csrf_token`) — consumed by every test file in Tasks 6–12 instead of each redefining its own login helper.

- [ ] **Step 1: Modify `webadmin/auth.py`** — `require_staff` seeds a CSRF token onto `request.state` for every staff page

Replace:
```python
async def require_staff(request: Request) -> None:
    if not is_staff(request):
        raise StaffLoginRequired()
```
with:
```python
async def require_staff(request: Request) -> None:
    if not is_staff(request):
        raise StaffLoginRequired()
    request.state.csrf_token = get_or_create_csrf_token(request)
```

Add the import this needs at the top of `webadmin/auth.py`: `from webadmin.csrf import get_or_create_csrf_token` (place it after the existing `from webadmin.config import ...` import; watch out for a circular import — `webadmin/csrf.py` only imports from `fastapi`, `hmac`, `secrets`, so importing it from `auth.py` is safe).

- [ ] **Step 2: Create `webadmin/templates/base.html`**

```html
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <title>{% block title %}WB Task Bot{% endblock %}</title>
    <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-gray-50 text-gray-900">
    <nav class="bg-slate-800 text-white px-6 py-3 flex gap-4 items-center flex-wrap">
        <a href="/schedule" class="font-semibold">📅 Расписание</a>
        <a href="/delivery-log" class="hover:underline">Журнал отправок</a>
        <a href="/status-history" class="hover:underline">История статусов</a>
        <a href="/tasks" class="hover:underline">Задачи</a>
        <a href="/users" class="hover:underline">Пользователи</a>
        <a href="/topics" class="hover:underline">Темы</a>
        <a href="/articles" class="hover:underline">Артикулы</a>
        <form action="/logout" method="post" class="ml-auto">
            <input type="hidden" name="csrf_token" value="{{ request.state.csrf_token }}">
            <button type="submit" class="hover:underline">Выйти</button>
        </form>
    </nav>
    <main class="p-6">
        {% block content %}{% endblock %}
    </main>
</body>
</html>
```

- [ ] **Step 3: Create `tests/webadmin/helpers.py`**

```python
import re


def extract_csrf_token(html: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match, "csrf_token hidden input not found in HTML"
    return match.group(1)


async def login_staff(client, password: str = "test-staff-pw") -> str:
    """Логинится и возвращает CSRF-токен сессии. Токен создаётся один раз на
    сессию (webadmin/csrf.py:get_or_create_csrf_token) и не меняется между
    запросами, поэтому тот же токен, что был на форме логина, годится и для
    любых последующих POST'ов (logout, CRUD-формы) в рамках того же client."""
    page = await client.get("/login")
    token = extract_csrf_token(page.text)
    await client.post("/login", data={"password": password, "csrf_token": token})
    return token


async def login_client(client, password: str = "test-client-pw") -> str:
    page = await client.get("/client/login")
    token = extract_csrf_token(page.text)
    await client.post("/client/login", data={"password": password, "csrf_token": token})
    return token
```

This mirrors the CSRF-fetching pattern already used inline in `tests/webadmin/test_auth.py` (Tasks 2/3) — those two files keep their own inline `_extract_csrf_token`, since they're testing the login mechanism itself and benefit from the explicit steps being visible. Every other test file (Task 6 onward) imports from here instead of redefining a local `_login`.

- [ ] **Step 4: Create `webadmin/templates/schedule.html`**

```html
{% extends "base.html" %}
{% block title %}Расписание{% endblock %}
{% block content %}
<div class="mb-4 flex items-center gap-3">
    <a href="/schedule?period=week" class="px-3 py-1 rounded {{ 'bg-slate-800 text-white' if period == 'week' else 'bg-gray-200' }}">Неделя</a>
    <a href="/schedule?period=month" class="px-3 py-1 rounded {{ 'bg-slate-800 text-white' if period == 'month' else 'bg-gray-200' }}">Месяц</a>
    <a href="/schedule?period={{ period }}&anchor={{ start.isoformat() }}&direction=-1" class="px-3 py-1 rounded bg-gray-200">◀</a>
    <span class="font-semibold">{{ start.strftime('%d.%m.%Y') }} — {{ end.strftime('%d.%m.%Y') }}</span>
    <a href="/schedule?period={{ period }}&anchor={{ start.isoformat() }}&direction=1" class="px-3 py-1 rounded bg-gray-200">▶</a>
</div>
<div class="overflow-x-auto">
<table class="min-w-full bg-white border border-gray-200 text-sm">
    <thead>
        <tr>
            <th class="border px-3 py-2 text-left">Задача</th>
            {% for d in days %}
            <th class="border px-2 py-2">{{ d.strftime('%d.%m') }}</th>
            {% endfor %}
        </tr>
    </thead>
    <tbody>
        {% for row in rows %}
        <tr>
            <td class="border px-3 py-2">{{ row.config.title }}</td>
            {% for d in days %}
            <td class="border px-2 py-2 text-center">
                {% if d in row.by_date %}{{ row.by_date[d].strftime('%H:%M') }}{% endif %}
            </td>
            {% endfor %}
        </tr>
        {% endfor %}
    </tbody>
</table>
</div>
{% endblock %}
```

- [ ] **Step 5: Create `webadmin/routers/reports.py`**

```python
from datetime import date, timedelta

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models import TaskConfig
from webadmin.auth import require_staff
from webadmin.deps import get_db
from webadmin.schedule_projection import (
    month_range, project_occurrences, shift_period, week_range,
)

router = APIRouter(dependencies=[Depends(require_staff)])
templates = Jinja2Templates(directory="webadmin/templates")


async def _schedule_context(session: AsyncSession, period: str, anchor: str | None,
                            direction: int) -> dict:
    ref = date.fromisoformat(anchor) if anchor else date.today()
    start, end = week_range(ref) if period == "week" else month_range(ref)
    if direction:
        start, end = shift_period(start, end, period, direction)
    configs = list(await session.scalars(
        select(TaskConfig).where(TaskConfig.is_active.is_(True))))
    rows = []
    for cfg in configs:
        occurrences = project_occurrences(cfg, start, end)
        by_date = {occ.date(): occ for occ in occurrences}
        rows.append({"config": cfg, "by_date": by_date})
    days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
    return {"period": period, "start": start, "end": end, "days": days, "rows": rows}


@router.get("/schedule", response_class=HTMLResponse)
async def schedule_page(request: Request, session: AsyncSession = Depends(get_db),
                        period: str = Query("week"), anchor: str | None = Query(None),
                        direction: int = Query(0)):
    ctx = await _schedule_context(session, period, anchor, direction)
    return templates.TemplateResponse("schedule.html", {"request": request, **ctx})
```

- [ ] **Step 6: Modify `webadmin/main.py`** — include the reports router

Add import: `from webadmin.routers.reports import router as reports_router`

Add after `app.include_router(auth_router)`:
```python
    app.include_router(reports_router)
```

- [ ] **Step 7: Write the failing tests**

Create `tests/webadmin/test_reports.py`:
```python
from datetime import datetime, time

from bot.database.models import TaskConfig
from tests.webadmin.helpers import login_staff


async def test_schedule_requires_staff_login(client):
    resp = await client.get("/schedule")
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"


async def test_schedule_shows_active_task_in_current_week(client, session_factory):
    async with session_factory() as session:
        session.add(TaskConfig(
            external_task_id="daily_task", title="Ежедневная задача",
            schedule_type="daily", time=time(9, 0), is_active=True))
        await session.commit()
    await login_staff(client)
    resp = await client.get("/schedule?period=week")
    assert resp.status_code == 200
    assert "Ежедневная задача" in resp.text


async def test_schedule_hides_inactive_task(client, session_factory):
    async with session_factory() as session:
        session.add(TaskConfig(
            external_task_id="inactive_task", title="Выключенная задача",
            schedule_type="daily", time=time(9, 0), is_active=False))
        await session.commit()
    await login_staff(client)
    resp = await client.get("/schedule?period=week")
    assert "Выключенная задача" not in resp.text


async def test_schedule_month_period_shows_more_days_than_week(client, session_factory):
    await login_staff(client)
    week_resp = await client.get("/schedule?period=week")
    month_resp = await client.get("/schedule?period=month")
    assert week_resp.text.count('class="border px-2 py-2">') < month_resp.text.count(
        'class="border px-2 py-2">')
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `pytest tests/webadmin/test_reports.py -v`
Expected: `4 passed`

- [ ] **Step 9: Commit**

```bash
git add webadmin tests/webadmin/helpers.py tests/webadmin/test_reports.py
git commit -m "feat(webadmin): staff /schedule calendar grid (week/month)"
```

---

### Task 6: `/client/schedule` client-facing page

**Files:**
- Create: `webadmin/templates/client_schedule.html`
- Modify: `webadmin/routers/reports.py`
- Modify: `webadmin/main.py`
- Test: `tests/webadmin/test_reports.py`

**Interfaces:**
- Consumes: `webadmin.auth.require_client` (Task 3), `_schedule_context` (Task 5, same file).
- Produces: `GET /client/schedule` route.

- [ ] **Step 1: Create `webadmin/templates/client_schedule.html`**

```html
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <title>Расписание задач</title>
    <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-gray-50 text-gray-900 p-6">
    <h1 class="text-2xl font-bold mb-4">📅 Расписание задач</h1>
    <div class="mb-4 flex items-center gap-3">
        <a href="/client/schedule?period=week" class="px-3 py-1 rounded {{ 'bg-slate-800 text-white' if period == 'week' else 'bg-gray-200' }}">Неделя</a>
        <a href="/client/schedule?period=month" class="px-3 py-1 rounded {{ 'bg-slate-800 text-white' if period == 'month' else 'bg-gray-200' }}">Месяц</a>
        <a href="/client/schedule?period={{ period }}&anchor={{ start.isoformat() }}&direction=-1" class="px-3 py-1 rounded bg-gray-200">◀</a>
        <span class="font-semibold">{{ start.strftime('%d.%m.%Y') }} — {{ end.strftime('%d.%m.%Y') }}</span>
        <a href="/client/schedule?period={{ period }}&anchor={{ start.isoformat() }}&direction=1" class="px-3 py-1 rounded bg-gray-200">▶</a>
    </div>
    <div class="overflow-x-auto">
    <table class="min-w-full bg-white border border-gray-200 text-sm">
        <thead>
            <tr>
                <th class="border px-3 py-2 text-left">Задача</th>
                {% for d in days %}
                <th class="border px-2 py-2">{{ d.strftime('%d.%m') }}</th>
                {% endfor %}
            </tr>
        </thead>
        <tbody>
            {% for row in rows %}
            <tr>
                <td class="border px-3 py-2">{{ row.config.title }}</td>
                {% for d in days %}
                <td class="border px-2 py-2 text-center">
                    {% if d in row.by_date %}{{ row.by_date[d].strftime('%H:%M') }}{% endif %}
                </td>
                {% endfor %}
            </tr>
            {% endfor %}
        </tbody>
    </table>
    </div>
</body>
</html>
```

- [ ] **Step 2: Modify `webadmin/routers/reports.py`** — add the client route

Add import at the top: `from webadmin.auth import require_client, require_staff` (replacing the `require_staff`-only import).

Add at the end of the file:
```python
@router.get("/client/schedule", response_class=HTMLResponse,
           dependencies=[Depends(require_client)])
async def client_schedule_page(request: Request, session: AsyncSession = Depends(get_db),
                               period: str = Query("week"), anchor: str | None = Query(None),
                               direction: int = Query(0)):
    ctx = await _schedule_context(session, period, anchor, direction)
    return templates.TemplateResponse("client_schedule.html", {"request": request, **ctx})
```

Note: the `router = APIRouter(dependencies=[Depends(require_staff)])` line still applies `require_staff` to every route on this router by default — the explicit `dependencies=[Depends(require_client)])` on this one route does NOT remove the router-level `require_staff`, both would run. Fix this by removing the router-level dependency and instead applying `Depends(require_staff)` explicitly to `schedule_page` only:

Replace:
```python
router = APIRouter(dependencies=[Depends(require_staff)])
```
with:
```python
router = APIRouter()
```

And change the `schedule_page` route decorator from:
```python
@router.get("/schedule", response_class=HTMLResponse)
```
to:
```python
@router.get("/schedule", response_class=HTMLResponse, dependencies=[Depends(require_staff)])
```

- [ ] **Step 3: Modify `webadmin/main.py`** — turn the Task 3 placeholder `/client` route into a redirect to the real schedule page

Replace:
```python
    @app.get("/client", response_class=PlainTextResponse, dependencies=[Depends(require_client)])
    async def client_home() -> str:
        return "ok, client"
```
with:
```python
    @app.get("/client", dependencies=[Depends(require_client)])
    async def client_home() -> RedirectResponse:
        return RedirectResponse(url="/client/schedule")
```

- [ ] **Step 4: Update the now-stale Task 3 test**

In `tests/webadmin/test_auth.py`, replace:
```python
async def test_client_login_correct_password_grants_access(client):
    form = await client.get("/client/login")
    token = _extract_csrf_token(form.text)
    resp = await client.post("/client/login", data={"password": "test-client-pw", "csrf_token": token})
    assert resp.status_code == 303
    assert resp.headers["location"] == "/client"
    home = await client.get("/client")
    assert home.status_code == 200
```
with:
```python
async def test_client_login_correct_password_grants_access(client):
    form = await client.get("/client/login")
    token = _extract_csrf_token(form.text)
    resp = await client.post("/client/login", data={"password": "test-client-pw", "csrf_token": token})
    assert resp.status_code == 303
    assert resp.headers["location"] == "/client"
    home = await client.get("/client")
    assert home.status_code == 307  # RedirectResponse default for GET
    assert home.headers["location"] == "/client/schedule"
```

- [ ] **Step 5: Write the failing test**

Add to the top of `tests/webadmin/test_reports.py`'s import block: `from tests.webadmin.helpers import login_client` (merge into the existing `from tests.webadmin.helpers import login_staff` line as `from tests.webadmin.helpers import login_client, login_staff`).

Add to `tests/webadmin/test_reports.py`:
```python
async def test_client_schedule_requires_client_login(client):
    resp = await client.get("/client/schedule")
    assert resp.status_code == 303
    assert resp.headers["location"] == "/client/login"


async def test_client_schedule_shows_active_task(client, session_factory):
    from datetime import time
    async with session_factory() as session:
        session.add(TaskConfig(
            external_task_id="daily_task", title="Ежедневная задача",
            schedule_type="daily", time=time(9, 0), is_active=True))
        await session.commit()
    await login_client(client)
    resp = await client.get("/client/schedule?period=week")
    assert resp.status_code == 200
    assert "Ежедневная задача" in resp.text


async def test_staff_session_does_not_grant_client_page_access(client):
    await login_staff(client)
    resp = await client.get("/client/schedule")
    assert resp.status_code == 303
    assert resp.headers["location"] == "/client/login"
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/webadmin/test_reports.py tests/webadmin/test_auth.py -v`
Expected: `19 passed` (7 in `test_reports.py` + 12 in `test_auth.py` — 6 from Task 2's staff login/CSRF + 6 from Task 3's client login/CSRF — all green, no regressions)

- [ ] **Step 7: Commit**

```bash
git add webadmin tests/webadmin
git commit -m "feat(webadmin): client-facing /client/schedule page"
```

---

### Task 7: `/delivery-log` staff report page

**Files:**
- Create: `webadmin/templates/delivery_log.html`
- Modify: `webadmin/routers/reports.py`
- Test: `tests/webadmin/test_reports.py`

**Interfaces:**
- Consumes: `bot.database.models.{TaskInstance, DeliveryStatus, Topic}` (existing).
- Produces: `GET /delivery-log` route.

- [ ] **Step 1: Create `webadmin/templates/delivery_log.html`**

```html
{% extends "base.html" %}
{% block title %}Журнал отправок{% endblock %}
{% block content %}
<form method="get" class="mb-4 flex items-center gap-3">
    <label>С <input type="date" name="start" value="{{ start.isoformat() }}" class="border rounded px-2 py-1"></label>
    <label>По <input type="date" name="end" value="{{ end.isoformat() }}" class="border rounded px-2 py-1"></label>
    <button type="submit" class="bg-slate-800 text-white px-3 py-1 rounded">Показать</button>
</form>
<table class="min-w-full bg-white border border-gray-200 text-sm">
    <thead>
        <tr>
            <th class="border px-3 py-2 text-left">Задача</th>
            <th class="border px-3 py-2 text-left">Чат/тема</th>
            <th class="border px-3 py-2 text-left">Время отправки</th>
            <th class="border px-3 py-2 text-left">Дедлайн</th>
        </tr>
    </thead>
    <tbody>
        {% for row in rows %}
        <tr>
            <td class="border px-3 py-2">{{ row.instance.title_snapshot }}</td>
            <td class="border px-3 py-2">{{ row.topic_name or "—" }}</td>
            <td class="border px-3 py-2">{{ row.instance.message_sent_at.strftime('%d.%m.%Y %H:%M') if row.instance.message_sent_at else "—" }}</td>
            <td class="border px-3 py-2">{{ row.instance.due_at.strftime('%d.%m.%Y %H:%M') if row.instance.due_at else "—" }}</td>
        </tr>
        {% endfor %}
    </tbody>
</table>
{% endblock %}
```

- [ ] **Step 2: Modify `webadmin/routers/reports.py`** — add the delivery-log route

Add imports at the top:
```python
from datetime import datetime, time

from bot.database.models import DeliveryStatus, TaskInstance, Topic
```

Add at the end of the file:
```python
@router.get("/delivery-log", response_class=HTMLResponse, dependencies=[Depends(require_staff)])
async def delivery_log_page(request: Request, session: AsyncSession = Depends(get_db),
                            start: str | None = Query(None), end: str | None = Query(None)):
    range_start = date.fromisoformat(start) if start else date.today().replace(day=1)
    range_end = date.fromisoformat(end) if end else date.today()
    stmt = (select(TaskInstance, Topic.topic_name)
            .outerjoin(Topic, TaskInstance.topic_id == Topic.id)
            .where(TaskInstance.delivery_status == DeliveryStatus.SENT,
                   TaskInstance.message_sent_at >= datetime.combine(range_start, time.min),
                   TaskInstance.message_sent_at <= datetime.combine(range_end, time.max))
            .order_by(TaskInstance.message_sent_at.desc()))
    rows = [{"instance": inst, "topic_name": topic_name}
           for inst, topic_name in (await session.execute(stmt)).all()]
    return templates.TemplateResponse("delivery_log.html", {
        "request": request, "rows": rows, "start": range_start, "end": range_end})
```

- [ ] **Step 3: Write the failing test**

Add to `tests/webadmin/test_reports.py`:
```python
async def test_delivery_log_shows_only_sent_instances_in_range(client, session_factory):
    from bot.database.models import TaskConfig, TaskInstance

    async with session_factory() as session:
        cfg = TaskConfig(external_task_id="t1", title="Task 1", schedule_type="daily",
                         time=time(9, 0), is_active=True)
        session.add(cfg)
        await session.flush()
        sent = TaskInstance(
            config_id=cfg.id, status="completed", scheduled_at=datetime(2026, 7, 10, 9, 0),
            scheduled_date=date(2026, 7, 10), schedule_key="k1", title_snapshot="Task 1",
            delivery_status="sent", message_sent_at=datetime(2026, 7, 10, 9, 0, 5))
        not_sent = TaskInstance(
            config_id=cfg.id, status="created", scheduled_at=datetime(2026, 7, 11, 9, 0),
            scheduled_date=date(2026, 7, 11), schedule_key="k2", title_snapshot="Task 1",
            delivery_status="pending")
        session.add_all([sent, not_sent])
        await session.commit()
    await login_staff(client)
    resp = await client.get("/delivery-log?start=2026-07-01&end=2026-07-31")
    assert resp.status_code == 200
    assert resp.text.count("Task 1") == 1  # только sent-инстанс попал в журнал
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/webadmin/test_reports.py::test_delivery_log_shows_only_sent_instances_in_range -v`
Expected: `1 passed`

- [ ] **Step 5: Commit**

```bash
git add webadmin tests/webadmin/test_reports.py
git commit -m "feat(webadmin): /delivery-log staff report page"
```

---

### Task 8: `/status-history` staff report page

**Files:**
- Create: `webadmin/templates/status_history.html`
- Modify: `webadmin/routers/reports.py`
- Test: `tests/webadmin/test_reports.py`

**Interfaces:**
- Consumes: `bot.database.models.{TaskLog, TaskInstance, User}` (existing).
- Produces: `GET /status-history` route.

- [ ] **Step 1: Create `webadmin/templates/status_history.html`**

```html
{% extends "base.html" %}
{% block title %}История статусов{% endblock %}
{% block content %}
<form method="get" class="mb-4 flex items-center gap-3">
    <label>С <input type="date" name="start" value="{{ start.isoformat() }}" class="border rounded px-2 py-1"></label>
    <label>По <input type="date" name="end" value="{{ end.isoformat() }}" class="border rounded px-2 py-1"></label>
    <button type="submit" class="bg-slate-800 text-white px-3 py-1 rounded">Показать</button>
</form>
<table class="min-w-full bg-white border border-gray-200 text-sm">
    <thead>
        <tr>
            <th class="border px-3 py-2 text-left">Задача</th>
            <th class="border px-3 py-2 text-left">Был статус</th>
            <th class="border px-3 py-2 text-left">Стал статус</th>
            <th class="border px-3 py-2 text-left">Кто</th>
            <th class="border px-3 py-2 text-left">Когда</th>
        </tr>
    </thead>
    <tbody>
        {% for row in rows %}
        <tr>
            <td class="border px-3 py-2">{{ row.title }}</td>
            <td class="border px-3 py-2">{{ row.log.old_status or "—" }}</td>
            <td class="border px-3 py-2">{{ row.log.new_status or "—" }}</td>
            <td class="border px-3 py-2">{{ row.who }}</td>
            <td class="border px-3 py-2">{{ row.log.created_at.strftime('%d.%m.%Y %H:%M') }}</td>
        </tr>
        {% endfor %}
    </tbody>
</table>
{% endblock %}
```

- [ ] **Step 2: Modify `webadmin/routers/reports.py`** — add the status-history route

Add a new import line at the top (a separate `from bot.database.models import ...` line is fine alongside the existing ones already in this file):
```python
from bot.database.models import TaskLog, User
```

Add at the end of the file:
```python
@router.get("/status-history", response_class=HTMLResponse, dependencies=[Depends(require_staff)])
async def status_history_page(request: Request, session: AsyncSession = Depends(get_db),
                               start: str | None = Query(None), end: str | None = Query(None)):
    range_start = date.fromisoformat(start) if start else date.today().replace(day=1)
    range_end = date.fromisoformat(end) if end else date.today()
    stmt = (select(TaskLog, TaskInstance.title_snapshot, User.name)
            .join(TaskInstance, TaskLog.task_instance_id == TaskInstance.id)
            .outerjoin(User, TaskLog.user_id == User.id)
            .where(TaskLog.created_at >= datetime.combine(range_start, time.min),
                   TaskLog.created_at <= datetime.combine(range_end, time.max))
            .order_by(TaskLog.created_at.desc()))
    rows = [{"log": log, "title": title, "who": name or log.action}
           for log, title, name in (await session.execute(stmt)).all()]
    return templates.TemplateResponse("status_history.html", {
        "request": request, "rows": rows, "start": range_start, "end": range_end})
```

- [ ] **Step 3: Write the failing test**

Add to `tests/webadmin/test_reports.py`:
```python
async def test_status_history_shows_transitions_with_actor_name(client, session_factory):
    from bot.database.models import TaskConfig, TaskInstance, TaskLog, User

    async with session_factory() as session:
        cfg = TaskConfig(external_task_id="t2", title="Task 2", schedule_type="daily",
                         time=time(9, 0), is_active=True)
        user = User(telegram_id=555, name="Валя", role="manager_wb")
        session.add_all([cfg, user])
        await session.flush()
        inst = TaskInstance(
            config_id=cfg.id, status="in_progress", scheduled_at=datetime(2026, 7, 10, 9, 0),
            scheduled_date=date(2026, 7, 10), schedule_key="k3", title_snapshot="Task 2")
        session.add(inst)
        await session.flush()
        session.add(TaskLog(task_instance_id=inst.id, user_id=user.id, action="btn:start",
                            old_status="created", new_status="in_progress",
                            created_at=datetime(2026, 7, 10, 9, 5)))
        session.add(TaskLog(task_instance_id=inst.id, user_id=None, action="auto:overdue",
                            old_status="in_progress", new_status="overdue",
                            created_at=datetime(2026, 7, 11, 9, 0)))
        await session.commit()
    await login_staff(client)
    resp = await client.get("/status-history?start=2026-07-01&end=2026-07-31")
    assert resp.status_code == 200
    assert "Валя" in resp.text        # известный пользователь — по имени
    assert "auto:overdue" in resp.text  # user_id is None — по action
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/webadmin/test_reports.py::test_status_history_shows_transitions_with_actor_name -v`
Expected: `1 passed`

- [ ] **Step 5: Commit**

```bash
git add webadmin tests/webadmin/test_reports.py
git commit -m "feat(webadmin): /status-history staff report page"
```

---

### Task 9: Users CRUD

> **Retrofit note (CSRF on CRUD forms):** Tasks 2/5 established CSRF for login/logout. This task (the first with real data-modifying forms) adds the last piece: a reusable `verify_csrf_form` dependency in `webadmin/csrf.py` that any POST route can drop in via `dependencies=[Depends(verify_csrf_form)]`, instead of every task hand-rolling its own check (this was a Minor finding from Task 2's review — centralize before the pattern repeats across Tasks 9-12). Every create/edit form embeds `{{ request.state.csrf_token }}` (already set by `require_staff` since Task 5 — no route needs to pass it into the template context explicitly).

**Files:**
- Modify: `webadmin/csrf.py` (add `verify_csrf_form` dependency)
- Modify: `bot/database/repositories/user_repository.py`
- Test: `tests/test_user_repository.py`
- Create: `webadmin/templates/users_list.html`
- Create: `webadmin/templates/user_form.html`
- Create: `webadmin/routers/users.py`
- Modify: `webadmin/main.py`
- Test: `tests/webadmin/test_users_crud.py`

**Interfaces:**
- Consumes: `UserRepository.{get_all, get_by_id, get_by_telegram_id, upsert}` (existing + one extension below), `webadmin.auth.require_staff` (Task 5, sets `request.state.csrf_token`), `tests.webadmin.helpers.login_staff` (Task 5, now returns the CSRF token).
- Produces: `UserRepository.upsert(..., private_chat_available: bool | None = None)`; `webadmin.csrf.verify_csrf_form` (FastAPI dependency — consumed by every POST route in Tasks 9-12); `GET/POST /users`, `/users/new`, `/users/{id}/edit` routes.

- [ ] **Step 1: Modify `webadmin/csrf.py`** — add the shared CSRF-verification dependency for POST routes

Add to the end of `webadmin/csrf.py`:
```python
from fastapi import Form, HTTPException


async def verify_csrf_form(request: Request, csrf_token: str = Form(...)) -> None:
    if not verify_csrf_token(request, csrf_token):
        raise HTTPException(status_code=400, detail="CSRF-токен недействителен")
```
(merge the `Form` import into whatever imports already exist at the top of the file from Task 2 — check first, don't duplicate `from fastapi import ...`.)

- [ ] **Step 2: Write the failing test for the repository extension**

Create `tests/test_user_repository.py`:
```python
from bot.database.repositories.user_repository import UserRepository


async def test_upsert_sets_private_chat_available_on_insert(session_factory):
    async with session_factory() as s:
        repo = UserRepository(s)
        user = await repo.upsert(111, "Аня", "manager_wb", private_chat_available=True)
        await s.commit()
        assert user.private_chat_available is True


async def test_upsert_without_flag_does_not_reset_existing_value(session_factory):
    """Sheets-синк (bot/services/google_sheets_service.py) вызывает upsert без
    этого параметра — апдейт не должен молча сбрасывать значение, выставленное
    где-то ещё (например, когда пользователь запускает /start в личке)."""
    async with session_factory() as s:
        repo = UserRepository(s)
        user = await repo.upsert(222, "Боря", "logistic", private_chat_available=True)
        await s.commit()
        await repo.upsert(222, "Боря", "logistic")  # без private_chat_available — как в sync
        await s.commit()
        assert user.private_chat_available is True


async def test_upsert_explicit_flag_updates_existing_value(session_factory):
    async with session_factory() as s:
        repo = UserRepository(s)
        user = await repo.upsert(333, "Вера", "owner", private_chat_available=False)
        await s.commit()
        await repo.upsert(333, "Вера", "owner", private_chat_available=True)
        await s.commit()
        assert user.private_chat_available is True
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/test_user_repository.py -v`
Expected: FAIL with `TypeError: upsert() got an unexpected keyword argument 'private_chat_available'`

- [ ] **Step 4: Modify `bot/database/repositories/user_repository.py`** — extend `upsert`

Replace the `upsert` method (currently lines 43-56):
```python
    async def upsert(self, telegram_id: int, name: str, role: str,
                      username: str | None = None, is_active: bool = True,
                      private_chat_available: bool | None = None) -> User:
        user = await self.get_by_telegram_id(telegram_id)
        if user is None:
            user = User(telegram_id=telegram_id, name=name, role=role,
                        username=username, is_active=is_active,
                        private_chat_available=bool(private_chat_available))
            self.session.add(user)
        else:
            user.name = name
            user.role = role
            user.username = username
            user.is_active = is_active
            if private_chat_available is not None:
                user.private_chat_available = private_chat_available
        await self.session.flush()
        return user
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_user_repository.py -v`
Expected: `3 passed`

- [ ] **Step 6: Run the full existing test suite to confirm no regression**

Run: `pytest -q`
Expected: all previously-passing tests still pass (the new parameter defaults to `None`, so every existing caller — Sheets sync, Telegram `/admin` — is unaffected).

- [ ] **Step 7: Create `webadmin/templates/users_list.html`**

```html
{% extends "base.html" %}
{% block title %}Пользователи{% endblock %}
{% block content %}
<div class="mb-4"><a href="/users/new" class="bg-slate-800 text-white px-3 py-2 rounded">+ Новый пользователь</a></div>
<table class="min-w-full bg-white border border-gray-200 text-sm">
    <thead><tr>
        <th class="border px-3 py-2">Telegram ID</th>
        <th class="border px-3 py-2">Имя</th>
        <th class="border px-3 py-2">Роль</th>
        <th class="border px-3 py-2">Активен</th>
        <th class="border px-3 py-2"></th>
    </tr></thead>
    <tbody>
        {% for u in users %}
        <tr>
            <td class="border px-3 py-2">{{ u.telegram_id }}</td>
            <td class="border px-3 py-2">{{ u.name }}</td>
            <td class="border px-3 py-2">{{ u.role }}</td>
            <td class="border px-3 py-2">{{ "Да" if u.is_active else "Нет" }}</td>
            <td class="border px-3 py-2"><a href="/users/{{ u.id }}/edit" class="text-blue-600 hover:underline">Редактировать</a></td>
        </tr>
        {% endfor %}
    </tbody>
</table>
{% endblock %}
```

- [ ] **Step 8: Create `webadmin/templates/user_form.html`**

```html
{% extends "base.html" %}
{% block title %}{{ "Редактировать" if user else "Новый" }} пользователь{% endblock %}
{% block content %}
<form method="post" class="bg-white p-6 rounded shadow-md max-w-md space-y-4">
    <input type="hidden" name="csrf_token" value="{{ request.state.csrf_token }}">
    {% if error %}<p class="text-red-600 text-sm">{{ error }}</p>{% endif %}
    {% if not user %}
    <div>
        <label class="block text-sm font-medium">Telegram ID</label>
        <input type="text" name="telegram_id" required class="w-full border rounded px-3 py-2">
    </div>
    {% endif %}
    <div>
        <label class="block text-sm font-medium">Имя</label>
        <input type="text" name="name" value="{{ user.name if user else '' }}" required class="w-full border rounded px-3 py-2">
    </div>
    <div>
        <label class="block text-sm font-medium">Роль</label>
        <select name="role" class="w-full border rounded px-3 py-2">
            {% for r in roles %}
            <option value="{{ r }}" {{ "selected" if user and user.role == r else "" }}>{{ r }}</option>
            {% endfor %}
        </select>
    </div>
    <label class="flex items-center gap-2">
        <input type="checkbox" name="is_active" {{ "checked" if not user or user.is_active else "" }}>
        Активен
    </label>
    <label class="flex items-center gap-2">
        <input type="checkbox" name="private_chat_available" {{ "checked" if user and user.private_chat_available else "" }}>
        Доступны личные сообщения
    </label>
    <button type="submit" class="bg-slate-800 text-white px-4 py-2 rounded">Сохранить</button>
</form>
{% endblock %}
```

- [ ] **Step 9: Create `webadmin/routers/users.py`**

```python
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.repositories.user_repository import UserRepository
from bot.utils.validation import validate_int
from webadmin.auth import require_staff
from webadmin.csrf import verify_csrf_form
from webadmin.deps import get_db

router = APIRouter(dependencies=[Depends(require_staff)])
templates = Jinja2Templates(directory="webadmin/templates")
ROLES = ["owner", "partner", "manager_wb", "logistic"]


@router.get("/users", response_class=HTMLResponse)
async def users_list(request: Request, session: AsyncSession = Depends(get_db)):
    users = await UserRepository(session).get_all(include_inactive=True)
    return templates.TemplateResponse("users_list.html", {"request": request, "users": users})


@router.get("/users/new", response_class=HTMLResponse)
async def user_new_form(request: Request):
    return templates.TemplateResponse("user_form.html", {
        "request": request, "user": None, "roles": ROLES, "error": None})


@router.post("/users/new", response_class=HTMLResponse, dependencies=[Depends(verify_csrf_form)])
async def user_create(request: Request, session: AsyncSession = Depends(get_db),
                      telegram_id: str = Form(...), name: str = Form(...),
                      role: str = Form(...), is_active: str | None = Form(None),
                      private_chat_available: str | None = Form(None)):
    repo = UserRepository(session)
    try:
        tg_id = validate_int(telegram_id)
    except ValueError as exc:
        return templates.TemplateResponse("user_form.html", {
            "request": request, "user": None, "roles": ROLES, "error": str(exc)}, status_code=400)
    if await repo.get_by_telegram_id(tg_id) is not None:
        return templates.TemplateResponse("user_form.html", {
            "request": request, "user": None, "roles": ROLES,
            "error": "Пользователь с таким telegram_id уже существует"}, status_code=400)
    await repo.upsert(tg_id, name, role, is_active=bool(is_active),
                      private_chat_available=bool(private_chat_available))
    await session.commit()
    return RedirectResponse(url="/users", status_code=303)


@router.get("/users/{user_id}/edit", response_class=HTMLResponse)
async def user_edit_form(user_id: int, request: Request, session: AsyncSession = Depends(get_db)):
    user = await UserRepository(session).get_by_id(user_id)
    if user is None:
        return RedirectResponse(url="/users", status_code=303)
    return templates.TemplateResponse("user_form.html", {
        "request": request, "user": user, "roles": ROLES, "error": None})


@router.post("/users/{user_id}/edit", response_class=HTMLResponse,
            dependencies=[Depends(verify_csrf_form)])
async def user_update(user_id: int, request: Request, session: AsyncSession = Depends(get_db),
                      name: str = Form(...), role: str = Form(...),
                      is_active: str | None = Form(None),
                      private_chat_available: str | None = Form(None)):
    repo = UserRepository(session)
    user = await repo.get_by_id(user_id)
    if user is None:
        return RedirectResponse(url="/users", status_code=303)
    await repo.upsert(user.telegram_id, name, role, username=user.username,
                      is_active=bool(is_active),
                      private_chat_available=bool(private_chat_available))
    await session.commit()
    return RedirectResponse(url="/users", status_code=303)
```

- [ ] **Step 10: Modify `webadmin/main.py`** — include the users router

Add import: `from webadmin.routers.users import router as users_router`

Add after `app.include_router(reports_router)`:
```python
    app.include_router(users_router)
```

- [ ] **Step 11: Write the failing tests**

Create `tests/webadmin/test_users_crud.py`:
```python
from tests.webadmin.helpers import login_staff


async def test_users_list_requires_login(client):
    resp = await client.get("/users")
    assert resp.status_code == 303


async def test_create_user_then_appears_in_list(client):
    token = await login_staff(client)
    resp = await client.post("/users/new", data={
        "telegram_id": "12345", "name": "Гоша", "role": "manager_wb", "is_active": "on",
        "csrf_token": token})
    assert resp.status_code == 303
    listing = await client.get("/users")
    assert "Гоша" in listing.text


async def test_create_user_duplicate_telegram_id_rejected(client):
    token = await login_staff(client)
    await client.post("/users/new", data={
        "telegram_id": "999", "name": "A", "role": "owner", "is_active": "on",
        "csrf_token": token})
    resp = await client.post("/users/new", data={
        "telegram_id": "999", "name": "B", "role": "owner", "is_active": "on",
        "csrf_token": token})
    assert resp.status_code == 400
    assert "уже существует" in resp.text


async def test_create_user_without_csrf_token_rejected(client):
    await login_staff(client)
    resp = await client.post("/users/new", data={
        "telegram_id": "444", "name": "Нет токена", "role": "owner", "is_active": "on"})
    assert resp.status_code == 422  # csrf_token: str = Form(...) is required


async def test_edit_user_updates_name(client, session_factory):
    from bot.database.repositories.user_repository import UserRepository

    async with session_factory() as session:
        user = await UserRepository(session).upsert(777, "Старое имя", "logistic")
        await session.commit()
        user_id = user.id
    token = await login_staff(client)
    resp = await client.post(f"/users/{user_id}/edit", data={
        "name": "Новое имя", "role": "logistic", "csrf_token": token})
    assert resp.status_code == 303
    listing = await client.get("/users")
    assert "Новое имя" in listing.text
    assert "Старое имя" not in listing.text
```

- [ ] **Step 12: Run tests to verify they pass**

Run: `pytest tests/webadmin/test_users_crud.py -v`
Expected: `5 passed`

- [ ] **Step 13: Commit**

```bash
git add bot/database/repositories/user_repository.py tests/test_user_repository.py webadmin tests/webadmin/test_users_crud.py
git commit -m "feat(webadmin): Users CRUD screens"
```

---

### Task 10: Topics CRUD

**Files:**
- Create: `webadmin/templates/topics_list.html`
- Create: `webadmin/templates/topic_form.html`
- Create: `webadmin/routers/topics.py`
- Modify: `webadmin/main.py`
- Test: `tests/webadmin/test_topics_crud.py`

**Interfaces:**
- Consumes: `TopicRepository.{get_all, get_by_id, get_by_key, upsert}` (existing, unmodified — already covers every field the form needs), `webadmin.csrf.verify_csrf_form` (Task 9).
- Produces: `GET/POST /topics`, `/topics/new`, `/topics/{id}/edit` routes.

- [ ] **Step 1: Create `webadmin/templates/topics_list.html`**

```html
{% extends "base.html" %}
{% block title %}Темы{% endblock %}
{% block content %}
<div class="mb-4"><a href="/topics/new" class="bg-slate-800 text-white px-3 py-2 rounded">+ Новая тема</a></div>
<table class="min-w-full bg-white border border-gray-200 text-sm">
    <thead><tr>
        <th class="border px-3 py-2">Ключ</th>
        <th class="border px-3 py-2">Название</th>
        <th class="border px-3 py-2">Thread ID</th>
        <th class="border px-3 py-2">Активна</th>
        <th class="border px-3 py-2"></th>
    </tr></thead>
    <tbody>
        {% for t in topics %}
        <tr>
            <td class="border px-3 py-2">{{ t.topic_key }}</td>
            <td class="border px-3 py-2">{{ t.topic_name }}</td>
            <td class="border px-3 py-2">{{ t.message_thread_id or "—" }}</td>
            <td class="border px-3 py-2">{{ "Да" if t.is_active else "Нет" }}</td>
            <td class="border px-3 py-2"><a href="/topics/{{ t.id }}/edit" class="text-blue-600 hover:underline">Редактировать</a></td>
        </tr>
        {% endfor %}
    </tbody>
</table>
{% endblock %}
```

- [ ] **Step 2: Create `webadmin/templates/topic_form.html`**

```html
{% extends "base.html" %}
{% block title %}{{ "Редактировать" if topic else "Новая" }} тема{% endblock %}
{% block content %}
<form method="post" class="bg-white p-6 rounded shadow-md max-w-md space-y-4">
    <input type="hidden" name="csrf_token" value="{{ request.state.csrf_token }}">
    {% if error %}<p class="text-red-600 text-sm">{{ error }}</p>{% endif %}
    {% if not topic %}
    <div>
        <label class="block text-sm font-medium">Ключ (topic_key)</label>
        <input type="text" name="topic_key" required class="w-full border rounded px-3 py-2">
    </div>
    {% endif %}
    <div>
        <label class="block text-sm font-medium">Название</label>
        <input type="text" name="topic_name" value="{{ topic.topic_name if topic else '' }}" required class="w-full border rounded px-3 py-2">
    </div>
    <div>
        <label class="block text-sm font-medium">Telegram thread ID</label>
        <input type="text" name="message_thread_id" value="{{ topic.message_thread_id if topic and topic.message_thread_id else '' }}" class="w-full border rounded px-3 py-2">
    </div>
    <div>
        <label class="block text-sm font-medium">Типы событий (через запятую)</label>
        <input type="text" name="event_types" value="{{ topic.event_types if topic and topic.event_types else '' }}" class="w-full border rounded px-3 py-2">
    </div>
    <label class="flex items-center gap-2">
        <input type="checkbox" name="is_active" {{ "checked" if not topic or topic.is_active else "" }}>
        Активна
    </label>
    <button type="submit" class="bg-slate-800 text-white px-4 py-2 rounded">Сохранить</button>
</form>
{% endblock %}
```

- [ ] **Step 3: Create `webadmin/routers/topics.py`**

```python
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.repositories.topic_repository import TopicRepository
from bot.utils.validation import validate_int
from webadmin.auth import require_staff
from webadmin.csrf import verify_csrf_form
from webadmin.deps import get_db

router = APIRouter(dependencies=[Depends(require_staff)])
templates = Jinja2Templates(directory="webadmin/templates")


@router.get("/topics", response_class=HTMLResponse)
async def topics_list(request: Request, session: AsyncSession = Depends(get_db)):
    topics = await TopicRepository(session).get_all(include_inactive=True)
    return templates.TemplateResponse("topics_list.html", {"request": request, "topics": topics})


@router.get("/topics/new", response_class=HTMLResponse)
async def topic_new_form(request: Request):
    return templates.TemplateResponse("topic_form.html", {
        "request": request, "topic": None, "error": None})


@router.post("/topics/new", response_class=HTMLResponse, dependencies=[Depends(verify_csrf_form)])
async def topic_create(request: Request, session: AsyncSession = Depends(get_db),
                       topic_key: str = Form(...), topic_name: str = Form(...),
                       message_thread_id: str = Form(""), event_types: str = Form(""),
                       is_active: str | None = Form(None)):
    repo = TopicRepository(session)
    if await repo.get_by_key(topic_key) is not None:
        return templates.TemplateResponse("topic_form.html", {
            "request": request, "topic": None,
            "error": "Тема с таким ключом уже существует"}, status_code=400)
    try:
        thread_id = validate_int(message_thread_id) if message_thread_id.strip() else None
    except ValueError as exc:
        return templates.TemplateResponse("topic_form.html", {
            "request": request, "topic": None, "error": str(exc)}, status_code=400)
    await repo.upsert(topic_key, topic_name, message_thread_id=thread_id,
                      event_types=event_types or None, is_active=bool(is_active))
    await session.commit()
    return RedirectResponse(url="/topics", status_code=303)


@router.get("/topics/{topic_id}/edit", response_class=HTMLResponse)
async def topic_edit_form(topic_id: int, request: Request, session: AsyncSession = Depends(get_db)):
    topic = await TopicRepository(session).get_by_id(topic_id)
    if topic is None:
        return RedirectResponse(url="/topics", status_code=303)
    return templates.TemplateResponse("topic_form.html", {
        "request": request, "topic": topic, "error": None})


@router.post("/topics/{topic_id}/edit", response_class=HTMLResponse,
            dependencies=[Depends(verify_csrf_form)])
async def topic_update(topic_id: int, request: Request, session: AsyncSession = Depends(get_db),
                       topic_name: str = Form(...), message_thread_id: str = Form(""),
                       event_types: str = Form(""), is_active: str | None = Form(None)):
    repo = TopicRepository(session)
    topic = await repo.get_by_id(topic_id)
    if topic is None:
        return RedirectResponse(url="/topics", status_code=303)
    try:
        thread_id = validate_int(message_thread_id) if message_thread_id.strip() else None
    except ValueError as exc:
        return templates.TemplateResponse("topic_form.html", {
            "request": request, "topic": topic, "error": str(exc)}, status_code=400)
    await repo.upsert(topic.topic_key, topic_name, message_thread_id=thread_id,
                      event_types=event_types or None, is_active=bool(is_active))
    await session.commit()
    return RedirectResponse(url="/topics", status_code=303)
```

- [ ] **Step 4: Modify `webadmin/main.py`** — include the topics router

Add import: `from webadmin.routers.topics import router as topics_router`

Add after `app.include_router(users_router)`:
```python
    app.include_router(topics_router)
```

- [ ] **Step 5: Write the failing tests**

Create `tests/webadmin/test_topics_crud.py`:
```python
from tests.webadmin.helpers import login_staff


async def test_create_topic_then_appears_in_list(client):
    token = await login_staff(client)
    resp = await client.post("/topics/new", data={
        "topic_key": "wb_ads", "topic_name": "Реклама WB", "is_active": "on",
        "csrf_token": token})
    assert resp.status_code == 303
    listing = await client.get("/topics")
    assert "Реклама WB" in listing.text


async def test_create_topic_duplicate_key_rejected(client):
    token = await login_staff(client)
    await client.post("/topics/new", data={
        "topic_key": "dup", "topic_name": "A", "is_active": "on", "csrf_token": token})
    resp = await client.post("/topics/new", data={
        "topic_key": "dup", "topic_name": "B", "is_active": "on", "csrf_token": token})
    assert resp.status_code == 400
    assert "уже существует" in resp.text


async def test_edit_topic_updates_name(client, session_factory):
    from bot.database.repositories.topic_repository import TopicRepository

    async with session_factory() as session:
        topic = await TopicRepository(session).upsert("edit_me", "Старое название")
        await session.commit()
        topic_id = topic.id
    token = await login_staff(client)
    resp = await client.post(f"/topics/{topic_id}/edit", data={
        "topic_name": "Новое название", "csrf_token": token})
    assert resp.status_code == 303
    listing = await client.get("/topics")
    assert "Новое название" in listing.text
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/webadmin/test_topics_crud.py -v`
Expected: `3 passed`

- [ ] **Step 7: Commit**

```bash
git add webadmin tests/webadmin/test_topics_crud.py
git commit -m "feat(webadmin): Topics CRUD screens"
```

---

### Task 11: Articles CRUD

**Files:**
- Modify: `bot/database/repositories/article_repository.py`
- Test: `tests/test_article_repository.py`
- Create: `webadmin/templates/articles_list.html`
- Create: `webadmin/templates/article_form.html`
- Create: `webadmin/routers/articles.py`
- Modify: `webadmin/main.py`
- Test: `tests/webadmin/test_articles_crud.py`

**Interfaces:**
- Consumes: `ArticleRepository.{get_all, get_by_article, upsert}` (existing), `webadmin.csrf.verify_csrf_form` (Task 9).
- Produces: `ArticleRepository.get_by_id(article_id: int) -> Article | None`; `GET/POST /articles`, `/articles/new`, `/articles/{id}/edit` routes.

- [ ] **Step 1: Write the failing test for the repository addition**

Create `tests/test_article_repository.py`:
```python
from bot.database.repositories.article_repository import ArticleRepository


async def test_get_by_id_returns_matching_article(session_factory):
    async with session_factory() as s:
        repo = ArticleRepository(s)
        created = await repo.upsert("12345678", product_name="Товар")
        await s.commit()
        found = await repo.get_by_id(created.id)
        assert found is not None
        assert found.article == "12345678"


async def test_get_by_id_returns_none_for_missing_id(session_factory):
    async with session_factory() as s:
        repo = ArticleRepository(s)
        assert await repo.get_by_id(999999) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_article_repository.py -v`
Expected: FAIL with `AttributeError: 'ArticleRepository' object has no attribute 'get_by_id'`

- [ ] **Step 3: Modify `bot/database/repositories/article_repository.py`** — add `get_by_id`

Add after the `get_by_article` method:
```python
    async def get_by_id(self, article_id: int) -> Article | None:
        return await self.session.get(Article, article_id)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_article_repository.py -v`
Expected: `2 passed`

- [ ] **Step 5: Create `webadmin/templates/articles_list.html`**

```html
{% extends "base.html" %}
{% block title %}Артикулы{% endblock %}
{% block content %}
<div class="mb-4"><a href="/articles/new" class="bg-slate-800 text-white px-3 py-2 rounded">+ Новый артикул</a></div>
<table class="min-w-full bg-white border border-gray-200 text-sm">
    <thead><tr>
        <th class="border px-3 py-2">Артикул</th>
        <th class="border px-3 py-2">Название товара</th>
        <th class="border px-3 py-2">Порядок</th>
        <th class="border px-3 py-2">Активен</th>
        <th class="border px-3 py-2"></th>
    </tr></thead>
    <tbody>
        {% for a in articles %}
        <tr>
            <td class="border px-3 py-2">{{ a.article }}</td>
            <td class="border px-3 py-2">{{ a.product_name or "—" }}</td>
            <td class="border px-3 py-2">{{ a.sort_order }}</td>
            <td class="border px-3 py-2">{{ "Да" if a.is_active else "Нет" }}</td>
            <td class="border px-3 py-2"><a href="/articles/{{ a.id }}/edit" class="text-blue-600 hover:underline">Редактировать</a></td>
        </tr>
        {% endfor %}
    </tbody>
</table>
{% endblock %}
```

- [ ] **Step 6: Create `webadmin/templates/article_form.html`**

```html
{% extends "base.html" %}
{% block title %}{{ "Редактировать" if article else "Новый" }} артикул{% endblock %}
{% block content %}
<form method="post" class="bg-white p-6 rounded shadow-md max-w-md space-y-4">
    <input type="hidden" name="csrf_token" value="{{ request.state.csrf_token }}">
    {% if error %}<p class="text-red-600 text-sm">{{ error }}</p>{% endif %}
    {% if not article %}
    <div>
        <label class="block text-sm font-medium">Артикул</label>
        <input type="text" name="article" required class="w-full border rounded px-3 py-2">
    </div>
    {% endif %}
    <div>
        <label class="block text-sm font-medium">Название товара</label>
        <input type="text" name="product_name" value="{{ article.product_name if article and article.product_name else '' }}" class="w-full border rounded px-3 py-2">
    </div>
    <div>
        <label class="block text-sm font-medium">Порядок сортировки</label>
        <input type="text" name="sort_order" value="{{ article.sort_order if article else 0 }}" class="w-full border rounded px-3 py-2">
    </div>
    <label class="flex items-center gap-2">
        <input type="checkbox" name="is_active" {{ "checked" if not article or article.is_active else "" }}>
        Активен
    </label>
    <button type="submit" class="bg-slate-800 text-white px-4 py-2 rounded">Сохранить</button>
</form>
{% endblock %}
```

- [ ] **Step 7: Create `webadmin/routers/articles.py`**

```python
from datetime import datetime

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.repositories.article_repository import ArticleRepository
from bot.utils.validation import validate_int
from webadmin.auth import require_staff
from webadmin.csrf import verify_csrf_form
from webadmin.deps import get_db

router = APIRouter(dependencies=[Depends(require_staff)])
templates = Jinja2Templates(directory="webadmin/templates")


@router.get("/articles", response_class=HTMLResponse)
async def articles_list(request: Request, session: AsyncSession = Depends(get_db)):
    articles = await ArticleRepository(session).get_all(include_inactive=True)
    return templates.TemplateResponse("articles_list.html", {
        "request": request, "articles": articles})


@router.get("/articles/new", response_class=HTMLResponse)
async def article_new_form(request: Request):
    return templates.TemplateResponse("article_form.html", {
        "request": request, "article": None, "error": None})


@router.post("/articles/new", response_class=HTMLResponse,
            dependencies=[Depends(verify_csrf_form)])
async def article_create(request: Request, session: AsyncSession = Depends(get_db),
                         article: str = Form(...), product_name: str = Form(""),
                         sort_order: str = Form("0"), is_active: str | None = Form(None)):
    repo = ArticleRepository(session)
    if await repo.get_by_article(article) is not None:
        return templates.TemplateResponse("article_form.html", {
            "request": request, "article": None,
            "error": "Артикул уже существует"}, status_code=400)
    try:
        order = validate_int(sort_order)
    except ValueError as exc:
        return templates.TemplateResponse("article_form.html", {
            "request": request, "article": None, "error": str(exc)}, status_code=400)
    await repo.upsert(article, product_name=product_name or None, sort_order=order,
                      is_active=bool(is_active), source="manual",
                      source_updated_at=datetime.utcnow())
    await session.commit()
    return RedirectResponse(url="/articles", status_code=303)


@router.get("/articles/{article_id}/edit", response_class=HTMLResponse)
async def article_edit_form(article_id: int, request: Request,
                            session: AsyncSession = Depends(get_db)):
    row = await ArticleRepository(session).get_by_id(article_id)
    if row is None:
        return RedirectResponse(url="/articles", status_code=303)
    return templates.TemplateResponse("article_form.html", {
        "request": request, "article": row, "error": None})


@router.post("/articles/{article_id}/edit", response_class=HTMLResponse,
            dependencies=[Depends(verify_csrf_form)])
async def article_update(article_id: int, request: Request,
                         session: AsyncSession = Depends(get_db),
                         product_name: str = Form(""), sort_order: str = Form("0"),
                         is_active: str | None = Form(None)):
    repo = ArticleRepository(session)
    row = await repo.get_by_id(article_id)
    if row is None:
        return RedirectResponse(url="/articles", status_code=303)
    try:
        order = validate_int(sort_order)
    except ValueError as exc:
        return templates.TemplateResponse("article_form.html", {
            "request": request, "article": row, "error": str(exc)}, status_code=400)
    await repo.upsert(row.article, product_name=product_name or None, sort_order=order,
                      is_active=bool(is_active), source="manual",
                      source_updated_at=datetime.utcnow())
    await session.commit()
    return RedirectResponse(url="/articles", status_code=303)
```

- [ ] **Step 8: Modify `webadmin/main.py`** — include the articles router

Add import: `from webadmin.routers.articles import router as articles_router`

Add after `app.include_router(topics_router)`:
```python
    app.include_router(articles_router)
```

- [ ] **Step 9: Write the failing tests**

Create `tests/webadmin/test_articles_crud.py`:
```python
from tests.webadmin.helpers import login_staff


async def test_create_article_then_appears_in_list(client):
    token = await login_staff(client)
    resp = await client.post("/articles/new", data={
        "article": "99988877", "product_name": "Тестовый товар",
        "sort_order": "1", "is_active": "on", "csrf_token": token})
    assert resp.status_code == 303
    listing = await client.get("/articles")
    assert "Тестовый товар" in listing.text


async def test_create_article_duplicate_rejected(client):
    token = await login_staff(client)
    await client.post("/articles/new", data={
        "article": "11122233", "product_name": "A", "sort_order": "0", "is_active": "on",
        "csrf_token": token})
    resp = await client.post("/articles/new", data={
        "article": "11122233", "product_name": "B", "sort_order": "0", "is_active": "on",
        "csrf_token": token})
    assert resp.status_code == 400
    assert "уже существует" in resp.text


async def test_edit_article_updates_product_name(client, session_factory):
    from bot.database.repositories.article_repository import ArticleRepository

    async with session_factory() as session:
        row = await ArticleRepository(session).upsert("44455566", product_name="Старое")
        await session.commit()
        article_id = row.id
    token = await login_staff(client)
    resp = await client.post(f"/articles/{article_id}/edit", data={
        "product_name": "Новое", "sort_order": "0", "is_active": "on", "csrf_token": token})
    assert resp.status_code == 303
    listing = await client.get("/articles")
    assert "Новое" in listing.text
```

- [ ] **Step 10: Run tests to verify they pass**

Run: `pytest tests/webadmin/test_articles_crud.py -v`
Expected: `3 passed`

- [ ] **Step 11: Commit**

```bash
git add bot/database/repositories/article_repository.py tests/test_article_repository.py webadmin tests/webadmin/test_articles_crud.py
git commit -m "feat(webadmin): Articles CRUD screens"
```

---

### Task 12: Tasks (TaskConfig) CRUD — the main form

**Files:**
- Create: `webadmin/templates/tasks_list.html`
- Create: `webadmin/templates/task_form.html`
- Create: `webadmin/routers/tasks.py`
- Modify: `webadmin/main.py`
- Test: `tests/webadmin/test_tasks_crud.py`

**Interfaces:**
- Consumes: `TaskRepository.{get_all_configs, get_config, upsert_config}` (existing), `bot.handlers.admin.task_configs._slugify_external_id(session, title)` (existing, reused as-is), `bot.services.scheduler_service.compute_next_run` (existing, with `after_change=True` from the earlier scheduler fix), `bot.utils.validation.{validate_int, validate_time_str}` (existing), `webadmin.csrf.verify_csrf_form` (Task 9).
- Produces: `GET/POST /tasks`, `/tasks/new`, `/tasks/{id}/edit` routes.

**Note on live scheduling:** saving a task here recomputes and persists `next_run_at` in the DB (so the data stays correct), but the Telegram bot's own running process only re-registers its in-memory APScheduler job for that config on its own restart or the next Sheets-sync-triggered `rebuild_config_job` — same known limitation as the existing `scripts/rebuild_all_schedules.py`. Not fixed here; out of scope for this plan (no cross-process notification was part of the approved spec).

- [ ] **Step 1: Create `webadmin/templates/tasks_list.html`**

```html
{% extends "base.html" %}
{% block title %}Задачи{% endblock %}
{% block content %}
<div class="mb-4"><a href="/tasks/new" class="bg-slate-800 text-white px-3 py-2 rounded">+ Новая задача</a></div>
<table class="min-w-full bg-white border border-gray-200 text-sm">
    <thead><tr>
        <th class="border px-3 py-2">Название</th>
        <th class="border px-3 py-2">Тип расписания</th>
        <th class="border px-3 py-2">Время</th>
        <th class="border px-3 py-2">Активна</th>
        <th class="border px-3 py-2"></th>
    </tr></thead>
    <tbody>
        {% for c in configs %}
        <tr>
            <td class="border px-3 py-2">{{ c.title }}</td>
            <td class="border px-3 py-2">{{ c.schedule_type }}</td>
            <td class="border px-3 py-2">{{ c.time.strftime('%H:%M') if c.time else "—" }}</td>
            <td class="border px-3 py-2">{{ "Да" if c.is_active else "Нет" }}</td>
            <td class="border px-3 py-2"><a href="/tasks/{{ c.id }}/edit" class="text-blue-600 hover:underline">Редактировать</a></td>
        </tr>
        {% endfor %}
    </tbody>
</table>
{% endblock %}
```

- [ ] **Step 2: Create `webadmin/templates/task_form.html`**

```html
{% extends "base.html" %}
{% block title %}{{ "Редактировать" if task else "Новая" }} задача{% endblock %}
{% block content %}
<form method="post" class="bg-white p-6 rounded shadow-md max-w-xl space-y-4">
    <input type="hidden" name="csrf_token" value="{{ request.state.csrf_token }}">
    {% if error %}<p class="text-red-600 text-sm">{{ error }}</p>{% endif %}
    <div>
        <label class="block text-sm font-medium">Название</label>
        <input type="text" name="title" value="{{ task.title if task else '' }}" required class="w-full border rounded px-3 py-2">
    </div>
    <div>
        <label class="block text-sm font-medium">Описание</label>
        <textarea name="description" class="w-full border rounded px-3 py-2">{{ task.description if task and task.description else '' }}</textarea>
    </div>
    <div>
        <label class="block text-sm font-medium">Сценарий</label>
        <select name="scenario" class="w-full border rounded px-3 py-2">
            <option value="simple" {{ "selected" if not task or task.scenario == "simple" else "" }}>simple</option>
            <option value="article_check" {{ "selected" if task and task.scenario == "article_check" else "" }}>article_check</option>
        </select>
    </div>
    <div>
        <label class="block text-sm font-medium">Ответственный (пользователь)</label>
        <select name="responsible_user_id" class="w-full border rounded px-3 py-2">
            <option value="">—</option>
            {% for u in users %}
            <option value="{{ u.id }}" {{ "selected" if task and task.responsible_user_id == u.id else "" }}>{{ u.name }}</option>
            {% endfor %}
        </select>
    </div>
    <div>
        <label class="block text-sm font-medium">Или роль</label>
        <select name="responsible_role" class="w-full border rounded px-3 py-2">
            <option value="">—</option>
            {% for r in roles %}
            <option value="{{ r }}" {{ "selected" if task and task.responsible_role == r else "" }}>{{ r }}</option>
            {% endfor %}
        </select>
    </div>
    <div>
        <label class="block text-sm font-medium">Тема</label>
        <select name="topic_id" class="w-full border rounded px-3 py-2">
            <option value="">—</option>
            {% for t in topics %}
            <option value="{{ t.id }}" {{ "selected" if task and task.topic_id == t.id else "" }}>{{ t.topic_name }}</option>
            {% endfor %}
        </select>
    </div>
    <div>
        <label class="block text-sm font-medium">Тип расписания</label>
        <select name="schedule_type" class="w-full border rounded px-3 py-2">
            {% for st in ["daily", "weekly", "monthly", "every_n_days", "cron"] %}
            <option value="{{ st }}" {{ "selected" if task and task.schedule_type == st else "" }}>{{ st }}</option>
            {% endfor %}
        </select>
    </div>
    <div>
        <label class="block text-sm font-medium">Значение расписания (дни недели 0-6 через запятую / день месяца / cron-выражение)</label>
        <input type="text" name="schedule_value" value="{{ task.schedule_value if task and task.schedule_value else '' }}" class="w-full border rounded px-3 py-2">
    </div>
    <div>
        <label class="block text-sm font-medium">Интервал в днях (для every_n_days)</label>
        <input type="text" name="schedule_interval" value="{{ task.schedule_interval if task and task.schedule_interval else '' }}" class="w-full border rounded px-3 py-2">
    </div>
    <div>
        <label class="block text-sm font-medium">Время отправки (ЧЧ:ММ)</label>
        <input type="text" name="time" value="{{ task.time.strftime('%H:%M') if task and task.time else '09:00' }}" required class="w-full border rounded px-3 py-2">
    </div>
    <div>
        <label class="block text-sm font-medium">Дедлайн (ЧЧ:ММ, пусто = сутки от отправки)</label>
        <input type="text" name="due_time" value="{{ task.due_time.strftime('%H:%M') if task and task.due_time else '' }}" class="w-full border rounded px-3 py-2">
    </div>
    <div>
        <label class="block text-sm font-medium">Дедлайн: дней после отправки</label>
        <input type="text" name="due_days_offset" value="{{ task.due_days_offset if task else 0 }}" class="w-full border rounded px-3 py-2">
    </div>
    <label class="flex items-center gap-2">
        <input type="checkbox" name="need_approval" {{ "checked" if task and task.need_approval else "" }}>
        Нужно подтверждение
    </label>
    <label class="flex items-center gap-2">
        <input type="checkbox" name="is_active" {{ "checked" if task and task.is_active else "" }}>
        Активна
    </label>
    <button type="submit" class="bg-slate-800 text-white px-4 py-2 rounded">Сохранить</button>
</form>
{% endblock %}
```

- [ ] **Step 3: Create `webadmin/routers/tasks.py`**

```python
from datetime import datetime

from apscheduler.triggers.cron import CronTrigger
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models import ScheduleType
from bot.database.repositories.task_repository import TaskRepository
from bot.database.repositories.topic_repository import TopicRepository
from bot.database.repositories.user_repository import UserRepository
from bot.handlers.admin.task_configs import _slugify_external_id
from bot.services.scheduler_service import compute_next_run
from bot.utils.validation import validate_int, validate_time_str
from webadmin.auth import require_staff
from webadmin.csrf import verify_csrf_form
from webadmin.deps import get_db

router = APIRouter(dependencies=[Depends(require_staff)])
templates = Jinja2Templates(directory="webadmin/templates")
ROLES = ["owner", "partner", "manager_wb", "logistic"]


def _parse_schedule(schedule_type: str, raw_value: str, raw_interval: str) -> tuple[str | None, int | None]:
    raw_value = raw_value.strip()
    if schedule_type == ScheduleType.WEEKLY:
        days = [d.strip() for d in raw_value.split(",") if d.strip()]
        if not days:
            raise ValueError("Укажите хотя бы один день недели (0=понедельник..6=воскресенье)")
        for d in days:
            validate_int(d, 0, 6)
        return ",".join(days), None
    if schedule_type == ScheduleType.MONTHLY:
        return str(validate_int(raw_value, 1, 31)), None
    if schedule_type == ScheduleType.EVERY_N_DAYS:
        return None, validate_int(raw_interval, 1)
    if schedule_type == ScheduleType.CRON:
        if not raw_value:
            raise ValueError("Введите cron-выражение")
        try:
            CronTrigger.from_crontab(raw_value)
        except Exception:
            raise ValueError("Некорректное cron-выражение") from None
        return raw_value, None
    return None, None  # daily


async def _render_form(request: Request, session: AsyncSession, task, error: str | None,
                       status_code: int = 200):
    users = await UserRepository(session).get_all(include_inactive=True)
    topics = await TopicRepository(session).get_all(include_inactive=True)
    return templates.TemplateResponse("task_form.html", {
        "request": request, "task": task, "users": users, "topics": topics,
        "roles": ROLES, "error": error}, status_code=status_code)


def _build_payload(external_task_id: str, title: str, description: str, scenario: str,
                   responsible_user_id: str, responsible_role: str, topic_id: str,
                   schedule_type: str, schedule_value: str, schedule_interval: str,
                   time_str: str, due_time_str: str, due_days_offset: str,
                   need_approval: str | None, is_active: str | None) -> dict:
    parsed_time = validate_time_str(time_str)
    parsed_due_time = validate_time_str(due_time_str) if due_time_str.strip() else None
    parsed_offset = validate_int(due_days_offset, 0, 30)
    value, interval = _parse_schedule(schedule_type, schedule_value, schedule_interval)
    return {
        "external_task_id": external_task_id,
        "title": title, "description": description or None, "scenario": scenario,
        "responsible_user_id": int(responsible_user_id) if responsible_user_id else None,
        "responsible_role": responsible_role or None,
        "topic_id": int(topic_id) if topic_id else None,
        "schedule_type": schedule_type, "schedule_value": value,
        "schedule_interval": interval, "time": parsed_time, "due_time": parsed_due_time,
        "due_days_offset": parsed_offset, "need_approval": bool(need_approval),
        "is_active": bool(is_active),
    }


@router.get("/tasks", response_class=HTMLResponse)
async def tasks_list(request: Request, session: AsyncSession = Depends(get_db)):
    configs = await TaskRepository(session).get_all_configs(include_inactive=True)
    return templates.TemplateResponse("tasks_list.html", {"request": request, "configs": configs})


@router.get("/tasks/new", response_class=HTMLResponse)
async def task_new_form(request: Request, session: AsyncSession = Depends(get_db)):
    return await _render_form(request, session, None, None)


@router.post("/tasks/new", response_class=HTMLResponse, dependencies=[Depends(verify_csrf_form)])
async def task_create(request: Request, session: AsyncSession = Depends(get_db),
                      title: str = Form(...), description: str = Form(""),
                      scenario: str = Form("simple"), responsible_user_id: str = Form(""),
                      responsible_role: str = Form(""), topic_id: str = Form(""),
                      schedule_type: str = Form(...), schedule_value: str = Form(""),
                      schedule_interval: str = Form(""), time: str = Form(...),
                      due_time: str = Form(""), due_days_offset: str = Form("0"),
                      need_approval: str | None = Form(None), is_active: str | None = Form(None)):
    try:
        external_id = await _slugify_external_id(session, title)
        payload = _build_payload(
            external_id, title, description, scenario, responsible_user_id,
            responsible_role, topic_id, schedule_type, schedule_value, schedule_interval,
            time, due_time, due_days_offset, need_approval, is_active)
    except ValueError as exc:
        return await _render_form(request, session, None, str(exc), status_code=400)
    cfg = await TaskRepository(session).upsert_config(payload)
    if cfg.is_active:
        cfg.next_run_at = compute_next_run(cfg, datetime.utcnow(), after_change=True)
    await session.commit()
    return RedirectResponse(url="/tasks", status_code=303)


@router.get("/tasks/{config_id}/edit", response_class=HTMLResponse)
async def task_edit_form(config_id: int, request: Request, session: AsyncSession = Depends(get_db)):
    task = await TaskRepository(session).get_config(config_id)
    if task is None:
        return RedirectResponse(url="/tasks", status_code=303)
    return await _render_form(request, session, task, None)


@router.post("/tasks/{config_id}/edit", response_class=HTMLResponse,
            dependencies=[Depends(verify_csrf_form)])
async def task_update(config_id: int, request: Request, session: AsyncSession = Depends(get_db),
                      title: str = Form(...), description: str = Form(""),
                      scenario: str = Form("simple"), responsible_user_id: str = Form(""),
                      responsible_role: str = Form(""), topic_id: str = Form(""),
                      schedule_type: str = Form(...), schedule_value: str = Form(""),
                      schedule_interval: str = Form(""), time: str = Form(...),
                      due_time: str = Form(""), due_days_offset: str = Form("0"),
                      need_approval: str | None = Form(None), is_active: str | None = Form(None)):
    repo = TaskRepository(session)
    task = await repo.get_config(config_id)
    if task is None:
        return RedirectResponse(url="/tasks", status_code=303)
    try:
        payload = _build_payload(
            task.external_task_id, title, description, scenario, responsible_user_id,
            responsible_role, topic_id, schedule_type, schedule_value, schedule_interval,
            time, due_time, due_days_offset, need_approval, is_active)
    except ValueError as exc:
        return await _render_form(request, session, task, str(exc), status_code=400)
    cfg = await repo.upsert_config(payload)
    cfg.next_run_at = (compute_next_run(cfg, datetime.utcnow(), after_change=True)
                      if cfg.is_active else None)
    await session.commit()
    return RedirectResponse(url="/tasks", status_code=303)
```

- [ ] **Step 4: Modify `webadmin/main.py`** — include the tasks router

Add import: `from webadmin.routers.tasks import router as tasks_router`

Add after `app.include_router(articles_router)`:
```python
    app.include_router(tasks_router)
```

- [ ] **Step 5: Write the failing tests**

Create `tests/webadmin/test_tasks_crud.py`:
```python
from tests.webadmin.helpers import login_staff


async def test_create_daily_task_then_appears_in_list(client):
    token = await login_staff(client)
    resp = await client.post("/tasks/new", data={
        "title": "Новая задача", "scenario": "simple", "responsible_user_id": "",
        "responsible_role": "", "topic_id": "", "schedule_type": "daily",
        "schedule_value": "", "schedule_interval": "", "time": "09:00",
        "due_time": "", "due_days_offset": "0", "is_active": "on", "csrf_token": token})
    assert resp.status_code == 303
    listing = await client.get("/tasks")
    assert "Новая задача" in listing.text


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


async def test_create_every_n_days_requires_interval(client):
    token = await login_staff(client)
    resp = await client.post("/tasks/new", data={
        "title": "Без интервала", "scenario": "simple", "responsible_user_id": "",
        "responsible_role": "", "topic_id": "", "schedule_type": "every_n_days",
        "schedule_value": "", "schedule_interval": "", "time": "09:00",
        "due_time": "", "due_days_offset": "0", "is_active": "on", "csrf_token": token})
    assert resp.status_code == 400


async def test_edit_task_updates_title(client, session_factory):
    from bot.database.models import TaskConfig

    async with session_factory() as session:
        cfg = TaskConfig(external_task_id="edit_me", title="Старое название",
                         schedule_type="daily", time=None, is_active=True)
        session.add(cfg)
        await session.commit()
        config_id = cfg.id
    token = await login_staff(client)
    resp = await client.post(f"/tasks/{config_id}/edit", data={
        "title": "Новое название", "scenario": "simple", "responsible_user_id": "",
        "responsible_role": "", "topic_id": "", "schedule_type": "daily",
        "schedule_value": "", "schedule_interval": "", "time": "10:00",
        "due_time": "", "due_days_offset": "0", "is_active": "on", "csrf_token": token})
    assert resp.status_code == 303
    listing = await client.get("/tasks")
    assert "Новое название" in listing.text
    assert "Старое название" not in listing.text
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/webadmin/test_tasks_crud.py -v`
Expected: `4 passed`

- [ ] **Step 7: Run the full test suite (bot + webadmin) to confirm no regressions anywhere**

Run: `pytest -q`
Expected: all tests pass (bot's existing suite + all new `tests/webadmin/*` files).

- [ ] **Step 8: Commit**

```bash
git add webadmin tests/webadmin/test_tasks_crud.py
git commit -m "feat(webadmin): Tasks (TaskConfig) CRUD screens"
```

---

### Task 13: Final manual smoke test across the whole app

**Files:** none (verification only).

**Interfaces:** none — this task exercises everything built in Tasks 1–12 end-to-end as a real user would.

- [ ] **Step 1: Run the full automated test suite one more time**

Run: `pytest -q`
Expected: all tests pass, including every `tests/webadmin/*` file created in this plan.

- [ ] **Step 2: Start the app locally against your real local Postgres**

Ensure your local `.env` has real `DATABASE_URL` (pointing at a local or dev Postgres — NOT the production Selectel database), `WEBADMIN_PASSWORD`, `CLIENT_PASSWORD`, `WEBADMIN_SECRET_KEY` set to real values.

Run: `uvicorn webadmin.main:app --reload --port 8080`

- [ ] **Step 3: Manually walk through the staff flow in a browser**

1. Open `http://localhost:8080/` — should redirect to `/login`.
2. Log in with your `WEBADMIN_PASSWORD` — should land on `/` (or navigate to `/schedule` via the nav bar).
3. Click through Расписание (toggle Неделя/Месяц, click ◀/▶), Журнал отправок, История статусов, Задачи, Пользователи, Темы, Артикулы.
4. Create one new Task, one new User, one new Topic, one new Article — verify each appears in its list.
5. Edit the Task you just created (change its title) — verify the change shows in the list.
6. Click Выйти (logout) — verify you're redirected to `/login` and `/schedule` now redirects there too.

- [ ] **Step 4: Manually verify the client flow separately**

1. Open `http://localhost:8080/client` in an incognito/private window — should redirect to `/client/login`.
2. Log in with `CLIENT_PASSWORD` — should land on `/client/schedule` showing the same calendar grid, styled standalone (no staff nav bar).
3. Confirm this client session cannot access `/schedule`, `/tasks`, `/users` etc. (should redirect to `/login`, not show data).

- [ ] **Step 5: Confirm no impact on the existing bot**

Run: `pytest tests/test_scheduler_service.py tests/test_sheets_service.py -q`
Expected: all pass unchanged — confirms the webadmin package didn't alter any bot behavior (the only bot-side changes in this plan are the two additive repository methods from Tasks 9 and 11, already covered by their own passing tests).

- [ ] **Step 6: Report completion**

No commit for this task (verification only) — if all manual checks pass, the plan is complete. Deployment to the Selectel server is a separate, explicit follow-up the user will request when ready.
