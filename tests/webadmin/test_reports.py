from datetime import datetime, time

from bot.database.models import TaskConfig
from tests.webadmin.helpers import login_client, login_staff


async def test_schedule_requires_staff_login(client):
    resp = await client.get("/schedule")
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"


async def test_schedule_shows_active_task_in_current_week(client, session_factory):
    async with session_factory() as session:
        session.add(TaskConfig(
            external_task_id="daily_task", title="Ежедневная задача",
            schedule_type="daily", time=time(9, 0), is_active=True))
        await session.commit()
    await login_staff(client)
    resp = await client.get("/schedule?period=week")
    assert resp.status_code == 200
    assert "Ежедневная задача" in resp.text


async def test_schedule_hides_inactive_task(client, session_factory):
    async with session_factory() as session:
        session.add(TaskConfig(
            external_task_id="inactive_task", title="Выключенная задача",
            schedule_type="daily", time=time(9, 0), is_active=False))
        await session.commit()
    await login_staff(client)
    resp = await client.get("/schedule?period=week")
    assert "Выключенная задача" not in resp.text


async def test_schedule_month_period_shows_more_days_than_week(client, session_factory):
    await login_staff(client)
    week_resp = await client.get("/schedule?period=week")
    month_resp = await client.get("/schedule?period=month")
    assert week_resp.text.count('class="border px-2 py-2">') < month_resp.text.count(
        'class="border px-2 py-2">')


async def test_client_schedule_requires_client_login(client):
    resp = await client.get("/client/schedule")
    assert resp.status_code == 303
    assert resp.headers["location"] == "/client/login"


async def test_client_schedule_shows_active_task(client, session_factory):
    from datetime import time
    async with session_factory() as session:
        session.add(TaskConfig(
            external_task_id="daily_task", title="Ежедневная задача",
            schedule_type="daily", time=time(9, 0), is_active=True))
        await session.commit()
    await login_client(client)
    resp = await client.get("/client/schedule?period=week")
    assert resp.status_code == 200
    assert "Ежедневная задача" in resp.text


async def test_staff_session_does_not_grant_client_page_access(client):
    await login_staff(client)
    resp = await client.get("/client/schedule")
    assert resp.status_code == 303
    assert resp.headers["location"] == "/client/login"
