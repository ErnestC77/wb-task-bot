from datetime import date, timedelta

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models import TaskConfig
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
