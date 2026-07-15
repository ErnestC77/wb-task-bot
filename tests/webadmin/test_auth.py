async def test_home_redirects_to_login_when_not_authenticated(client):
    resp = await client.get("/")
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"


async def test_login_wrong_password_shows_error(client):
    resp = await client.post("/login", data={"password": "wrong"})
    assert resp.status_code == 401
    assert "Неверный пароль" in resp.text


async def test_login_correct_password_grants_access(client):
    resp = await client.post("/login", data={"password": "test-staff-pw"})
    assert resp.status_code == 303
    assert resp.headers["location"] == "/"
    home = await client.get("/")
    assert home.status_code == 200
    assert home.text == "ok, staff"


async def test_logout_revokes_access(client):
    await client.post("/login", data={"password": "test-staff-pw"})
    await client.post("/logout")
    resp = await client.get("/")
    assert resp.status_code == 303
