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

    async def list_page(self, page: int, page_size: int,
                         actor_user_id: int | None = None) -> list[AdminAuditLog]:
        stmt = select(AdminAuditLog)
        if actor_user_id is not None:
            stmt = stmt.where(AdminAuditLog.actor_user_id == actor_user_id)
        stmt = (stmt.order_by(AdminAuditLog.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size))
        return list(await self.session.scalars(stmt))
