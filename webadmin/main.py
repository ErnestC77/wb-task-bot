from fastapi import Depends, FastAPI, Request
from fastapi.responses import PlainTextResponse, RedirectResponse
from starlette.middleware.sessions import SessionMiddleware

from webadmin.auth import ClientLoginRequired, StaffLoginRequired, require_client, require_staff
from webadmin.config import get_webadmin_settings
from webadmin.routers.auth import router as auth_router
from webadmin.routers.articles import router as articles_router
from webadmin.routers.reports import router as reports_router
from webadmin.routers.tasks import router as tasks_router
from webadmin.routers.topics import router as topics_router
from webadmin.routers.users import router as users_router


def create_app() -> FastAPI:
    app = FastAPI(title="WB Task Bot — веб-админка")
    # https_only=False is intentional while this runs on local http://; MUST become True
    # before any real deploy behind HTTPS — do not flip this without confirming TLS is
    # actually in front of the app.
    app.add_middleware(
        SessionMiddleware,
        secret_key=get_webadmin_settings().webadmin_secret_key,
        same_site="strict",
        max_age=8 * 3600,
        https_only=False,
    )

    @app.exception_handler(StaffLoginRequired)
    async def _staff_login_required(request: Request, exc: StaffLoginRequired):
        return RedirectResponse(url="/login", status_code=303)

    @app.exception_handler(ClientLoginRequired)
    async def _client_login_required(request: Request, exc: ClientLoginRequired):
        return RedirectResponse(url="/client/login", status_code=303)

    @app.get("/healthz", response_class=PlainTextResponse)
    async def healthz() -> str:
        return "ok"

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
