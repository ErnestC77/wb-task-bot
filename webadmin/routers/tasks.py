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
