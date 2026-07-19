import json

from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.repositories.audit_repository import AuditRepository


class AuditService:
    def __init__(self, session: AsyncSession) -> None:
        self.repo = AuditRepository(session)

    async def log(self, actor_user_id: int | None, action: str, *,
                  entity_type: str | None = None, entity_id: str | None = None,
                  setting_key: str | None = None, old_value: object = None,
                  new_value: object = None, result: str = "ok",
                  ip_or_source: str | None = None) -> None:
        await self.repo.add(
            actor_user_id=actor_user_id, action=action, entity_type=entity_type,
            entity_id=entity_id, setting_key=setting_key,
            old_value_json=None if old_value is None else json.dumps(old_value, ensure_ascii=False),
            new_value_json=None if new_value is None else json.dumps(new_value, ensure_ascii=False),
            result=result, ip_or_source=ip_or_source)
