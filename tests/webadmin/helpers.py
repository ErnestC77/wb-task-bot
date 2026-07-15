import re


def extract_csrf_token(html: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match, "csrf_token hidden input not found in HTML"
    return match.group(1)


async def login_staff(client, password: str = "test-staff-pw") -> str:
    """Логинится и возвращает CSRF-токен сессии. Токен создаётся один раз на
    сессию (webadmin/csrf.py:get_or_create_csrf_token) и не меняется между
    запросами, поэтому тот же токен, что был на форме логина, годится и для
    любых последующих POST'ов (logout, CRUD-формы) в рамках того же client."""
    page = await client.get("/login")
    token = extract_csrf_token(page.text)
    await client.post("/login", data={"password": password, "csrf_token": token})
    return token


async def login_client(client, password: str = "test-client-pw") -> str:
    page = await client.get("/client/login")
    token = extract_csrf_token(page.text)
    await client.post("/client/login", data={"password": password, "csrf_token": token})
    return token
