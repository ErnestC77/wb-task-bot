from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models import Article


class ArticleRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_active(self, sort_order: bool = True) -> list[Article]:
        stmt = select(Article).where(Article.is_active.is_(True))
        if sort_order:
            stmt = stmt.order_by(Article.sort_order)
        return list(await self.session.scalars(stmt))

    async def get_all(self, include_inactive: bool = False) -> list[Article]:
        """Все артикулы (по умолчанию только активные), отсортированные по
        sort_order — для списков в админ-панели (Task 29), тот же паттерн, что
        UserRepository.get_all/TopicRepository.get_all/TaskRepository.get_all_configs."""
        stmt = select(Article)
        if not include_inactive:
            stmt = stmt.where(Article.is_active.is_(True))
        return list(await self.session.scalars(stmt.order_by(Article.sort_order)))

    async def get_by_article(self, article: str) -> Article | None:
        return await self.session.scalar(
            select(Article).where(Article.article == article))

    async def get_active_not_in(self, articles: set[str]) -> list[Article]:
        """Активные записи, отсутствующие среди articles (Task 22: чтобы
        деактивировать записи, не встретившиеся в свежей выгрузке Sheets)."""
        stmt = select(Article).where(Article.is_active.is_(True))
        if articles:
            stmt = stmt.where(Article.article.not_in(articles))
        return list(await self.session.scalars(stmt))

    async def upsert(self, article: str, product_name: str | None = None,
                     is_active: bool = True, sort_order: int = 0,
                     responsible_user_id: int | None = None,
                     responsible_role: str | None = None,
                     source: str = "google_sheets",
                     source_updated_at=None) -> Article:
        row = await self.get_by_article(article)
        if row is None:
            row = Article(article=article, product_name=product_name,
                          is_active=is_active, sort_order=sort_order,
                          responsible_user_id=responsible_user_id,
                          responsible_role=responsible_role, source=source,
                          source_updated_at=source_updated_at)
            self.session.add(row)
        else:
            row.product_name = product_name
            row.is_active = is_active
            row.sort_order = sort_order
            row.responsible_user_id = responsible_user_id
            row.responsible_role = responsible_role
            row.source = source
            row.source_updated_at = source_updated_at
        await self.session.flush()
        return row

    async def deactivate(self, article_id: int) -> None:
        row = await self.session.get(Article, article_id)
        if row is not None:
            row.is_active = False
            await self.session.flush()

    async def list_page(self, page: int, size: int,
                        include_inactive: bool = False) -> list[Article]:
        stmt = select(Article)
        if not include_inactive:
            stmt = stmt.where(Article.is_active.is_(True))
        stmt = (stmt.order_by(Article.sort_order)
                .offset((page - 1) * size).limit(size))
        return list(await self.session.scalars(stmt))
