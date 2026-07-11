from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models import AdminAuditLog


class AuditRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add(self, **fields) -> AdminAuditLog:
        entry = AdminAuditLog(**fields)
        self.session.add(entry)
        await self.session.flush()
        return entry

    async def get_last_by_action(self, action: str,
                                 result: str | None = None) -> AdminAuditLog | None:
        """Task 22: последняя запись по action (опционально фильтр по result) —
        используется для определения момента «последней синхронизации» при
        разрешении конфликтов (conflict_policy=admin_wins)."""
        stmt = select(AdminAuditLog).where(AdminAuditLog.action == action)
        if result is not None:
            stmt = stmt.where(AdminAuditLog.result == result)
        stmt = stmt.order_by(AdminAuditLog.id.desc()).limit(1)
        return await self.session.scalar(stmt)

    async def list_page(self, page: int, page_size: int,
                         actor_user_id: int | None = None) -> list[AdminAuditLog]:
        stmt = select(AdminAuditLog)
        if actor_user_id is not None:
            stmt = stmt.where(AdminAuditLog.actor_user_id == actor_user_id)
        stmt = (stmt.order_by(AdminAuditLog.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size))
        return list(await self.session.scalars(stmt))
