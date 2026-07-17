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


async def test_create_task_sets_pending_rebuild_when_active(client, session_factory):
    """Веб-форма больше не считает next_run_at сама — только выставляет
    pending_rebuild, единственный пересчёт делает bot's rebuild_config_job
    (см. docs/superpowers/plans/2026-07-15-webadmin-live-schedule-pickup.md)."""
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
        assert cfg.pending_rebuild is True


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


# ---------------------------------------------------------------------------
# МСК<->UTC для поля time (баг: планировщик работает в naive-UTC, а admin
# вводит время по МСК через веб-форму — без конвертации задача уходила на
# 3 часа позже). Тестируем _build_payload напрямую, без HTTP-клиента: он
# уже задет отдельным (существовавшим до этого фикса) сбоем шаблонизатора
# в тестовом окружении (Jinja2 LRUCache), не связанным с этим багом.
# ---------------------------------------------------------------------------

def test_build_payload_converts_time_msk_to_utc():
    from datetime import time
    from webadmin.routers.tasks import _build_payload
    payload = _build_payload(
        "ext", "Т", "", "simple", "", "", "", "daily", "", "",
        "09:30", "", "0", None, None)
    assert payload["time"] == time(6, 30)
