from tests.webadmin.helpers import login_staff


async def test_users_list_requires_login(client):
    resp = await client.get("/users")
    assert resp.status_code == 303


async def test_create_user_then_appears_in_list(client):
    token = await login_staff(client)
    resp = await client.post("/users/new", data={
        "telegram_id": "12345", "name": "Гоша", "role": "manager_wb", "is_active": "on",
        "csrf_token": token})
    assert resp.status_code == 303
    listing = await client.get("/users")
    assert "Гоша" in listing.text


async def test_create_user_duplicate_telegram_id_rejected(client):
    token = await login_staff(client)
    await client.post("/users/new", data={
        "telegram_id": "999", "name": "A", "role": "owner", "is_active": "on",
        "csrf_token": token})
    resp = await client.post("/users/new", data={
        "telegram_id": "999", "name": "B", "role": "owner", "is_active": "on",
        "csrf_token": token})
    assert resp.status_code == 400
    assert "уже существует" in resp.text


async def test_create_user_without_csrf_token_rejected(client):
    await login_staff(client)
    resp = await client.post("/users/new", data={
        "telegram_id": "444", "name": "Нет токена", "role": "owner", "is_active": "on"})
    assert resp.status_code == 422  # csrf_token: str = Form(...) is required


async def test_edit_user_updates_name(client, session_factory):
    from bot.database.repositories.user_repository import UserRepository

    async with session_factory() as session:
        user = await UserRepository(session).upsert(777, "Старое имя", "logistic")
        await session.commit()
        user_id = user.id
    token = await login_staff(client)
    resp = await client.post(f"/users/{user_id}/edit", data={
        "name": "Новое имя", "role": "logistic", "csrf_token": token})
    assert resp.status_code == 303
    listing = await client.get("/users")
    assert "Новое имя" in listing.text
    assert "Старое имя" not in listing.text
