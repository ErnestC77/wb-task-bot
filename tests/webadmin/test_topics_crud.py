from tests.webadmin.helpers import login_staff


async def test_create_topic_then_appears_in_list(client):
    token = await login_staff(client)
    resp = await client.post("/topics/new", data={
        "topic_key": "wb_ads", "topic_name": "Реклама WB", "is_active": "on",
        "csrf_token": token})
    assert resp.status_code == 303
    listing = await client.get("/topics")
    assert "Реклама WB" in listing.text


async def test_create_topic_duplicate_key_rejected(client):
    token = await login_staff(client)
    await client.post("/topics/new", data={
        "topic_key": "dup", "topic_name": "A", "is_active": "on", "csrf_token": token})
    resp = await client.post("/topics/new", data={
        "topic_key": "dup", "topic_name": "B", "is_active": "on", "csrf_token": token})
    assert resp.status_code == 400
    assert "уже существует" in resp.text


async def test_edit_topic_updates_name(client, session_factory):
    from bot.database.repositories.topic_repository import TopicRepository

    async with session_factory() as session:
        topic = await TopicRepository(session).upsert("edit_me", "Старое название")
        await session.commit()
        topic_id = topic.id
    token = await login_staff(client)
    resp = await client.post(f"/topics/{topic_id}/edit", data={
        "topic_name": "Новое название", "csrf_token": token})
    assert resp.status_code == 303
    listing = await client.get("/topics")
    assert "Новое название" in listing.text
