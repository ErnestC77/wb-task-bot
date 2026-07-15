import hmac
import secrets

from fastapi import Request

CSRF_SESSION_KEY = "csrf_token"


def get_or_create_csrf_token(request: Request) -> str:
    """Возвращает CSRF-токен текущей сессии, создавая его при первом обращении
    (обычно на GET формы) — один токен на сессию, не на форму."""
    token = request.session.get(CSRF_SESSION_KEY)
    if not token:
        token = secrets.token_urlsafe(32)
        request.session[CSRF_SESSION_KEY] = token
    return token


def verify_csrf_token(request: Request, submitted: str | None) -> bool:
    expected = request.session.get(CSRF_SESSION_KEY)
    return bool(expected) and bool(submitted) and hmac.compare_digest(expected, submitted)
