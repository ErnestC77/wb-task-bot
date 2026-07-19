from datetime import datetime

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.repositories.article_repository import ArticleRepository
from bot.utils.validation import validate_int
from webadmin.audit import log_create, log_edit, snapshot
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
    row = await repo.upsert(article, product_name=product_name or None, sort_order=order,
                            is_active=bool(is_active), source="manual",
                            source_updated_at=datetime.utcnow())
    await log_create(session, "article", row.id, dict(
        article=article, product_name=product_name or None, sort_order=order,
        is_active=bool(is_active)))
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
    fields = ["product_name", "sort_order", "is_active"]
    old = snapshot(row, fields)                        # ДО upsert — мутирует row in-place
    await repo.upsert(row.article, product_name=product_name or None, sort_order=order,
                      is_active=bool(is_active), source="manual",
                      source_updated_at=datetime.utcnow())
    new = dict(product_name=product_name or None, sort_order=order, is_active=bool(is_active))
    await log_edit(session, "article", article_id, old, new)
    await session.commit()
    return RedirectResponse(url="/articles", status_code=303)
