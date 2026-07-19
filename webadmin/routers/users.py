from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.repositories.user_repository import UserRepository
from bot.utils.validation import validate_int
from webadmin.audit import log_create, log_edit, snapshot
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
    user = await repo.upsert(tg_id, name, role, is_active=bool(is_active),
                             private_chat_available=bool(private_chat_available))
    await log_create(session, "user", user.id, dict(
        telegram_id=tg_id, name=name, role=role, is_active=bool(is_active),
        private_chat_available=bool(private_chat_available)))
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
    fields = ["name", "role", "is_active", "private_chat_available"]
    old = snapshot(user, fields)                       # ДО upsert — мутирует user in-place
    await repo.upsert(user.telegram_id, name, role, username=user.username,
                      is_active=bool(is_active),
                      private_chat_available=bool(private_chat_available))
    new = dict(name=name, role=role, is_active=bool(is_active),
              private_chat_available=bool(private_chat_available))
    await log_edit(session, "user", user_id, old, new)
    await session.commit()
    return RedirectResponse(url="/users", status_code=303)
