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
