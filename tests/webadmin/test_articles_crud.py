from tests.webadmin.helpers import login_staff


async def test_create_article_then_appears_in_list(client):
    token = await login_staff(client)
    resp = await client.post("/articles/new", data={
        "article": "99988877", "product_name": "Тестовый товар",
        "sort_order": "1", "is_active": "on", "csrf_token": token})
    assert resp.status_code == 303
    listing = await client.get("/articles")
    assert "Тестовый товар" in listing.text


async def test_create_article_duplicate_rejected(client):
    token = await login_staff(client)
    await client.post("/articles/new", data={
        "article": "11122233", "product_name": "A", "sort_order": "0", "is_active": "on",
        "csrf_token": token})
    resp = await client.post("/articles/new", data={
        "article": "11122233", "product_name": "B", "sort_order": "0", "is_active": "on",
        "csrf_token": token})
    assert resp.status_code == 400
    assert "уже существует" in resp.text


async def test_edit_article_updates_product_name(client, session_factory):
    from bot.database.repositories.article_repository import ArticleRepository

    async with session_factory() as session:
        row = await ArticleRepository(session).upsert("44455566", product_name="Старое")
        await session.commit()
        article_id = row.id
    token = await login_staff(client)
    resp = await client.post(f"/articles/{article_id}/edit", data={
        "product_name": "Новое", "sort_order": "0", "is_active": "on", "csrf_token": token})
    assert resp.status_code == 303
    listing = await client.get("/articles")
    assert "Новое" in listing.text
