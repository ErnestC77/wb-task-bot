from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models import Article, User
from bot.database.repositories.article_repository import ArticleRepository
from bot.services.setting_service import SettingService

# article_check.sort_order choices: "sort_order" | "article" | "name" (=> product_name)
_SORT_COLUMNS = {
    "sort_order": Article.sort_order,
    "article": Article.article,
    "name": Article.product_name,
}


class ArticleService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = ArticleRepository(session)
        self.settings = SettingService(session)

    async def get_active_for_check(self) -> list[Article]:
        """Активные для проверки артикулы. include_archived=True добавляет и
        деактивированные (архивные) артикулы; сортировка — по настройке
        article_check.sort_order."""
        include_archived = bool(await self.settings.get("article_check.include_archived"))
        sort_key = await self.settings.get("article_check.sort_order")
        column = _SORT_COLUMNS.get(sort_key, Article.sort_order)
        stmt = select(Article)
        if not include_archived:
            stmt = stmt.where(Article.is_active.is_(True))
        stmt = stmt.order_by(column)
        return list(await self.session.scalars(stmt))

    async def upsert_from_sheet(self, rows: list[dict]) -> dict:
        """Синхронизация из Google Sheets (Task 22): апсерт по коду артикула,
        деактивация активных артикулов, отсутствующих в rows."""
        seen: set[str] = set()
        created = 0
        updated = 0
        for i, row in enumerate(rows):
            code = row["article"]
            seen.add(code)
            existing = await self.repo.get_by_article(code)
            await self.repo.upsert(
                article=code,
                product_name=row.get("product_name"),
                is_active=row.get("is_active", True),
                sort_order=row.get("sort_order", i),
                responsible_user_id=row.get("responsible_user_id"),
                responsible_role=row.get("responsible_role"),
                source="google_sheets",
                source_updated_at=row.get("source_updated_at", datetime.utcnow()))
            if existing is None:
                created += 1
            else:
                updated += 1
        deactivated = 0
        for a in await self.repo.get_active():
            if a.article not in seen:
                await self.repo.deactivate(a.id)
                deactivated += 1
        return {"created": created, "updated": updated, "deactivated": deactivated,
                "total_rows": len(rows)}

    async def list_page(self, page: int, size: int,
                        include_inactive: bool = False) -> list[Article]:
        return await self.repo.list_page(page, size, include_inactive)

    async def add_manual(self, article: str, name: str, actor: User) -> Article:
        allowed = bool(await self.settings.get("article_check.allow_manual_article_add"))
        if not allowed:
            raise PermissionError("Ручное добавление артикулов запрещено настройкой")
        return await self.repo.upsert(article=article, product_name=name,
                                      is_active=True, source="manual")
