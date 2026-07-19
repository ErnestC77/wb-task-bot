from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.repositories.topic_repository import TopicRepository
from bot.utils.validation import validate_int
from webadmin.audit import log_create, log_edit, snapshot
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
    topic = await repo.upsert(topic_key, topic_name, message_thread_id=thread_id,
                              event_types=event_types or None, is_active=bool(is_active))
    await log_create(session, "topic", topic.id, dict(
        topic_key=topic_key, topic_name=topic_name, message_thread_id=thread_id,
        event_types=event_types or None, is_active=bool(is_active)))
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
    fields = ["topic_name", "message_thread_id", "event_types", "is_active"]
    old = snapshot(topic, fields)                      # ДО upsert — мутирует topic in-place
    await repo.upsert(topic.topic_key, topic_name, message_thread_id=thread_id,
                      event_types=event_types or None, is_active=bool(is_active))
    new = dict(topic_name=topic_name, message_thread_id=thread_id,
              event_types=event_types or None, is_active=bool(is_active))
    await log_edit(session, "topic", topic_id, old, new)
    await session.commit()
    return RedirectResponse(url="/topics", status_code=303)
