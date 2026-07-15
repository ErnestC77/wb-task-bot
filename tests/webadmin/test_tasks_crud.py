from tests.webadmin.helpers import login_staff


async def test_create_daily_task_then_appears_in_list(client):
    token = await login_staff(client)
    resp = await client.post("/tasks/new", data={
        "title": "Новая задача", "scenario": "simple", "responsible_user_id": "",
        "responsible_role": "", "topic_id": "", "schedule_type": "daily",
        "schedule_value": "", "schedule_interval": "", "time": "09:00",
        "due_time": "", "due_days_offset": "0", "is_active": "on", "csrf_token": token})
    assert resp.status_code == 303
    listing = await client.get("/tasks")
    assert "Новая задача" in listing.text


async def test_create_task_sets_next_run_at_for_today_when_active(client, session_factory):
    token = await login_staff(client)
    resp = await client.post("/tasks/new", data={
        "title": "Активная задача", "scenario": "simple", "responsible_user_id": "",
        "responsible_role": "", "topic_id": "", "schedule_type": "daily",
        "schedule_value": "", "schedule_interval": "", "time": "23:59",
        "due_time": "", "due_days_offset": "0", "is_active": "on", "csrf_token": token})
    assert resp.status_code == 303
    from sqlalchemy import select
    from bot.database.models import TaskConfig
    async with session_factory() as session:
        cfg = await session.scalar(select(TaskConfig).where(TaskConfig.title == "Активная задача"))
        assert cfg.next_run_at is not None


async def test_create_every_n_days_requires_interval(client):
    token = await login_staff(client)
    resp = await client.post("/tasks/new", data={
        "title": "Без интервала", "scenario": "simple", "responsible_user_id": "",
        "responsible_role": "", "topic_id": "", "schedule_type": "every_n_days",
        "schedule_value": "", "schedule_interval": "", "time": "09:00",
        "due_time": "", "due_days_offset": "0", "is_active": "on", "csrf_token": token})
    assert resp.status_code == 400


async def test_edit_task_updates_title(client, session_factory):
    from bot.database.models import TaskConfig

    async with session_factory() as session:
        cfg = TaskConfig(external_task_id="edit_me", title="Старое название",
                         schedule_type="daily", time=None, is_active=True)
        session.add(cfg)
        await session.commit()
        config_id = cfg.id
    token = await login_staff(client)
    resp = await client.post(f"/tasks/{config_id}/edit", data={
        "title": "Новое название", "scenario": "simple", "responsible_user_id": "",
        "responsible_role": "", "topic_id": "", "schedule_type": "daily",
        "schedule_value": "", "schedule_interval": "", "time": "10:00",
        "due_time": "", "due_days_offset": "0", "is_active": "on", "csrf_token": token})
    assert resp.status_code == 303
    listing = await client.get("/tasks")
    assert "Новое название" in listing.text
    assert "Старое название" not in listing.text
