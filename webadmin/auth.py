from fastapi import Request

from webadmin.config import get_webadmin_settings

STAFF_SESSION_KEY = "staff_authenticated"


class StaffLoginRequired(Exception):
    pass


def check_staff_password(password: str) -> bool:
    return password == get_webadmin_settings().webadmin_password


def is_staff(request: Request) -> bool:
    return bool(request.session.get(STAFF_SESSION_KEY))


async def require_staff(request: Request) -> None:
    if not is_staff(request):
        raise StaffLoginRequired()
