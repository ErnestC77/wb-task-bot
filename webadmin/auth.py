import hmac

from fastapi import Request

from webadmin.config import get_webadmin_settings
from webadmin.csrf import get_or_create_csrf_token

STAFF_SESSION_KEY = "staff_authenticated"


class StaffLoginRequired(Exception):
    pass


def check_staff_password(password: str) -> bool:
    return hmac.compare_digest(password, get_webadmin_settings().webadmin_password)


def is_staff(request: Request) -> bool:
    return bool(request.session.get(STAFF_SESSION_KEY))


async def require_staff(request: Request) -> None:
    if not is_staff(request):
        raise StaffLoginRequired()
    request.state.csrf_token = get_or_create_csrf_token(request)


CLIENT_SESSION_KEY = "client_authenticated"


class ClientLoginRequired(Exception):
    pass


def check_client_password(password: str) -> bool:
    return hmac.compare_digest(password, get_webadmin_settings().client_password)


def is_client(request: Request) -> bool:
    return bool(request.session.get(CLIENT_SESSION_KEY))


async def require_client(request: Request) -> None:
    if not is_client(request):
        raise ClientLoginRequired()
