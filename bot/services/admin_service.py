import secrets
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models import User
from bot.services.permission_service import PermissionService

SECTION_PERMISSIONS: dict[str, str] = {
    "set": "settings.manage", "usr": "users.manage", "top": "topics.manage",
    "cfg": "tasks.manage", "sch": "schedules.manage", "art": "articles.manage",
    "dic": "dictionaries.manage", "qst": "questions.manage", "rep": "reports.manage",
    "syn": "sync.run", "ops": "tasks.run_manual", "aud": "audit.view",
}

# Дольше держать «Вы уверены? ✅/❌» смысла нет — TTL защищает от протухших/
# забытых токенов, которые иначе висели бы в _pending_confirms бесконечно
# (Task 23 security fix).
CONFIRM_TTL_SECONDS = 15 * 60


@dataclass
class _PendingConfirm:
    # Task 27 review fix (Critical): `op` больше НЕ замыкание без параметров,
    # захватывающее `session` из запроса-инициатора — та сессия закрывается
    # `DbSessionMiddleware` (bot/loader.py) до того, как приходит подтверждение
    # (это ДРУГОЙ Telegram-апдейт, с ДРУГОЙ AsyncSession). `op` теперь ПРИНИМАЕТ
    # сессию параметром и обязана использовать ИМЕННО её для всех операций с БД
    # (флаши/запросы через репозитории/commit) — эту сессию `execute_confirmed`
    # получает от `handle_confirm`, т.е. из ТЕКУЩЕГО (подтверждающего) запроса.
    # На реальном Postgres захват чужой сессии приводил к тому, что мутации
    # op() открывали НЕЗАКОММИЧЕННУЮ транзакцию на уже закрытой сессии, а
    # commit() в handle_confirm коммитил ДРУГУЮ (свежую, без изменений) сессию —
    # изменения тихо терялись, плюс утечка соединения в пуле.
    op: Callable[[AsyncSession], Awaitable[None]]
    required_permission: str   # право, необходимое для ВЫПОЛНЕНИЯ операции за токеном
    creator_actor_id: int      # кто инициировал (аудит/на будущее; НЕ обязателен
                                # для подтверждения — см. handle_confirm)
    created_at: float


_pending_confirms: dict[str, _PendingConfirm] = {}


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

    def confirm_token(self, action: str, op: Callable[[AsyncSession], Awaitable[None]],
                      required_permission: str, creator_actor_id: int) -> str:
        """Регистрирует отложенную опасную операцию.

        `op` ОБЯЗАНА принимать сессию параметром (`op(session)`) и использовать
        ИМЕННО её для всех операций с БД — НЕ захватывать `session` текущего
        (инициирующего) запроса через замыкание. `execute_confirmed` вызывает
        `op(session)` с сессией ТОГО запроса, в котором пришло подтверждение —
        она гарантированно открыта и валидна (см. docstring `_PendingConfirm.op`
        и `execute_confirmed` ниже — Task 27 review fix, Critical).

        `required_permission` ОБЯЗАН быть проверен у того, кто подтверждает
        (см. `handle_confirm` в `bot/handlers/admin/main.py`) — до этого фикса
        подтверждение проверяло только факт регистрации пользователя, из-за
        чего любой активный аккаунт (даже без единого права в системе) мог
        подтвердить чужую привилегированную операцию.

        Task 24 fix: `action` может содержать ":" (например
        "settings.reset.approval.timeout_hours" — по мотивам ключей
        SETTINGS_REGISTRY), а токен уходит в `ConfirmCb.t` и пакуется через
        aiogram `CallbackData.pack()`, чей разделитель полей — ':'. Если
        итоговый token содержит ':', `pack()` бросает ValueError, и
        confirm_keyboard() ни разу не сможет отрисовать кнопку подтверждения
        для такой операции. Поэтому action санитизируется, а разделитель
        между action и случайным суффиксом — '_', а не ':'.
        """
        safe_action = action.replace(":", "_")
        token = f"{safe_action}_{secrets.token_urlsafe(8)}"[:32]
        _pending_confirms[token] = _PendingConfirm(
            op=op, required_permission=required_permission,
            creator_actor_id=creator_actor_id, created_at=time.monotonic())
        return token

    def get_pending(self, token: str) -> _PendingConfirm | None:
        """Читает отложенную операцию БЕЗ извлечения — чтобы handle_confirm мог
        проверить право/TTL до выполнения, не теряя токен при отказе."""
        return _pending_confirms.get(token)

    @staticmethod
    def is_expired(entry: _PendingConfirm) -> bool:
        return time.monotonic() - entry.created_at > CONFIRM_TTL_SECONDS

    def discard(self, token: str) -> None:
        _pending_confirms.pop(token, None)

    async def execute_confirmed(self, token: str, session: AsyncSession) -> bool:
        """`session` ОБЯЗАНА быть сессией ТЕКУЩЕГО (подтверждающего) запроса —
        именно её `handle_confirm` получает от `DbSessionMiddleware` для этого
        конкретного Telegram-апдейта. Передаётся в `entry.op(session)` вместо
        того, чтобы `op` захватывала стороннюю сессию через замыкание (Task 27
        review fix, Critical — см. docstring `_PendingConfirm.op`)."""
        entry = _pending_confirms.pop(token, None)   # одноразовый токен — идемпотентно
        if entry is None or self.is_expired(entry):
            return False
        await entry.op(session)
        return True
