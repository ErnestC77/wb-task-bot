import secrets
from collections.abc import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models import User
from bot.services.permission_service import PermissionService

SECTION_PERMISSIONS: dict[str, str] = {
    "set": "settings.manage", "usr": "users.manage", "top": "topics.manage",
    "cfg": "tasks.manage", "sch": "schedules.manage", "art": "articles.manage",
    "dic": "dictionaries.manage", "qst": "questions.manage", "rep": "reports.manage",
    "syn": "sync.run", "ops": "tasks.run_manual", "aud": "audit.view",
}

_pending_confirms: dict[str, Callable[[], Awaitable[None]]] = {}


class AdminService:
    def __init__(self, session: AsyncSession, bot=None, scheduler=None) -> None:
        self.session = session
        self.bot = bot
        self.scheduler = scheduler
        self.permissions = PermissionService(session)

    async def visible_sections(self, user: User) -> set[str]:
        result = set()
        for section, permission in SECTION_PERMISSIONS.items():
            if await self.permissions.has_permission(user, permission):
                result.add(section)
        return result

    def confirm_token(self, action: str,
                      op: Callable[[], Awaitable[None]]) -> str:
        token = f"{action}:{secrets.token_urlsafe(8)}"[:32]
        _pending_confirms[token] = op
        return token

    async def execute_confirmed(self, token: str) -> bool:
        op = _pending_confirms.pop(token, None)   # одноразовый токен — идемпотентно
        if op is None:
            return False
        await op()
        return True
