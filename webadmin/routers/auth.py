from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from webadmin.auth import STAFF_SESSION_KEY, check_staff_password
from webadmin.csrf import get_or_create_csrf_token, verify_csrf_token

router = APIRouter()
templates = Jinja2Templates(directory="webadmin/templates")


@router.get("/login", response_class=HTMLResponse)
async def login_form(request: Request):
    csrf_token = get_or_create_csrf_token(request)
    return templates.TemplateResponse(
        "login.html", {"request": request, "error": None, "csrf_token": csrf_token})


@router.post("/login", response_class=HTMLResponse)
async def login_submit(request: Request, password: str = Form(...), csrf_token: str = Form(...)):
    if not verify_csrf_token(request, csrf_token):
        new_csrf_token = get_or_create_csrf_token(request)
        return templates.TemplateResponse(
            "login.html",
            {
                "request": request,
                "error": "Сессия истекла, попробуйте войти ещё раз",
                "csrf_token": new_csrf_token,
            },
            status_code=400,
        )
    if not check_staff_password(password):
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": "Неверный пароль", "csrf_token": csrf_token},
            status_code=401,
        )
    request.session[STAFF_SESSION_KEY] = True
    return RedirectResponse(url="/", status_code=303)


@router.post("/logout")
async def logout(request: Request, csrf_token: str = Form(None)):
    if not verify_csrf_token(request, csrf_token):
        return PlainTextResponse("CSRF-токен недействителен", status_code=400)
    request.session.pop(STAFF_SESSION_KEY, None)
    return RedirectResponse(url="/login", status_code=303)
