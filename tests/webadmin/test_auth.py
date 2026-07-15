import re


def _extract_csrf_token(html: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match, "csrf_token hidden input not found in login page HTML"
    return match.group(1)


async def test_home_redirects_to_login_when_not_authenticated(client):
    resp = await client.get("/")
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"


async def test_login_wrong_password_shows_error(client):
    login_page = await client.get("/login")
    csrf_token = _extract_csrf_token(login_page.text)

    resp = await client.post("/login", data={"password": "wrong", "csrf_token": csrf_token})
    assert resp.status_code == 401
    assert "Неверный пароль" in resp.text


async def test_login_correct_password_grants_access(client):
    login_page = await client.get("/login")
    csrf_token = _extract_csrf_token(login_page.text)

    resp = await client.post(
        "/login", data={"password": "test-staff-pw", "csrf_token": csrf_token})
    assert resp.status_code == 303
    assert resp.headers["location"] == "/"
    home = await client.get("/")
    assert home.status_code == 200
    assert home.text == "ok, staff"


async def test_login_without_csrf_token_rejected(client):
    resp = await client.post("/login", data={"password": "test-staff-pw"})
    # csrf_token: str = Form(...) is a required field — FastAPI/Starlette reject a
    # missing required form field with 422 (validation error), not 400.
    assert resp.status_code == 422


async def test_login_with_wrong_csrf_token_rejected(client):
    login_page = await client.get("/login")
    _extract_csrf_token(login_page.text)  # establishes a session with a real token

    resp = await client.post(
        "/login", data={"password": "test-staff-pw", "csrf_token": "forged"})
    assert resp.status_code == 400
    assert "Сессия истекла" in resp.text


async def test_logout_revokes_access(client):
    login_page = await client.get("/login")
    csrf_token = _extract_csrf_token(login_page.text)
    await client.post("/login", data={"password": "test-staff-pw", "csrf_token": csrf_token})

    login_page_again = await client.get("/login")
    logout_csrf_token = _extract_csrf_token(login_page_again.text)

    await client.post("/logout", data={"csrf_token": logout_csrf_token})
    resp = await client.get("/")
    assert resp.status_code == 303
