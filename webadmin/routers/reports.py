from datetime import date, datetime, time, timedelta

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models import DeliveryStatus, TaskConfig, TaskInstance, TaskLog, Topic, User
from webadmin.auth import require_client, require_staff
from webadmin.deps import get_db
from webadmin.schedule_projection import (
    month_range, project_occurrences, shift_period, week_range,
)

router = APIRouter()
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


def _parse_date_range(start: str | None, end: str | None) -> tuple[date, date]:
    range_start = date.fromisoformat(start) if start else date.today().replace(day=1)
    range_end = date.fromisoformat(end) if end else date.today()
    return range_start, range_end


@router.get("/schedule", response_class=HTMLResponse, dependencies=[Depends(require_staff)])
async def schedule_page(request: Request, session: AsyncSession = Depends(get_db),
                        period: str = Query("week"), anchor: str | None = Query(None),
                        direction: int = Query(0)):
    ctx = await _schedule_context(session, period, anchor, direction)
    return templates.TemplateResponse("schedule.html", {"request": request, **ctx})


@router.get("/client/schedule", response_class=HTMLResponse,
           dependencies=[Depends(require_client)])
async def client_schedule_page(request: Request, session: AsyncSession = Depends(get_db),
                               period: str = Query("week"), anchor: str | None = Query(None),
                               direction: int = Query(0)):
    ctx = await _schedule_context(session, period, anchor, direction)
    return templates.TemplateResponse("client_schedule.html", {"request": request, **ctx})


@router.get("/delivery-log", response_class=HTMLResponse, dependencies=[Depends(require_staff)])
async def delivery_log_page(request: Request, session: AsyncSession = Depends(get_db),
                            start: str | None = Query(None), end: str | None = Query(None)):
    range_start, range_end = _parse_date_range(start, end)
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


@router.get("/status-history", response_class=HTMLResponse, dependencies=[Depends(require_staff)])
async def status_history_page(request: Request, session: AsyncSession = Depends(get_db),
                               start: str | None = Query(None), end: str | None = Query(None)):
    range_start, range_end = _parse_date_range(start, end)
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
