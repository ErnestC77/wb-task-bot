from fastapi import Depends, FastAPI, Request
from fastapi.responses import PlainTextResponse, RedirectResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.middleware.sessions import SessionMiddleware

from webadmin.auth import ClientLoginRequired, StaffLoginRequired, require_client, require_staff
from webadmin.config import get_webadmin_settings
from webadmin.deps import get_db
from webadmin.routers.auth import router as auth_router
from webadmin.routers.articles import router as articles_router
from webadmin.routers.reports import router as reports_router
from webadmin.routers.tasks import router as tasks_router
from webadmin.routers.topics import router as topics_router
from webadmin.routers.users import router as users_router


def create_app() -> FastAPI:
    app = FastAPI(title="WB Task Bot — веб-админка")
    settings = get_webadmin_settings()
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.webadmin_secret_key,
        same_site="strict",
        max_age=8 * 3600,
        https_only=settings.webadmin_https_only,
    )

    @app.exception_handler(StaffLoginRequired)
    async def _staff_login_required(request: Request, exc: StaffLoginRequired):
        return RedirectResponse(url="/login", status_code=303)

    @app.exception_handler(ClientLoginRequired)
    async def _client_login_required(request: Request, exc: ClientLoginRequired):
        return RedirectResponse(url="/client/login", status_code=303)

    @app.get("/healthz")
    async def healthz(session: AsyncSession = Depends(get_db)):
        # Раньше docker-compose вообще не проверял webadmin (только
        # restart: unless-stopped при падении процесса) — реальный запрос
        # к БД, а не просто "процесс жив", ловит зависшее соединение с
        # Postgres так же, как heartbeat_job у bot-сервиса (см. инцидент
        # 2026-07-17 про healthcheck, который ничего не проверял).
        await session.execute(text("SELECT 1"))
        return PlainTextResponse("ok")

    @app.get("/", dependencies=[Depends(require_staff)])
    async def home() -> RedirectResponse:
        return RedirectResponse(url="/schedule")

    @app.get("/client", dependencies=[Depends(require_client)])
    async def client_home() -> RedirectResponse:
        return RedirectResponse(url="/client/schedule")

    app.include_router(auth_router)
    app.include_router(reports_router)
    app.include_router(users_router)
    app.include_router(topics_router)
    app.include_router(articles_router)
    app.include_router(tasks_router)
    return app


app = create_app()
