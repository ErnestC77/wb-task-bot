from bot.database.repositories.article_repository import ArticleRepository


async def test_get_by_id_returns_matching_article(session_factory):
    async with session_factory() as s:
        repo = ArticleRepository(s)
        created = await repo.upsert("12345678", product_name="Товар")
        await s.commit()
        found = await repo.get_by_id(created.id)
        assert found is not None
        assert found.article == "12345678"


async def test_get_by_id_returns_none_for_missing_id(session_factory):
    async with session_factory() as s:
        repo = ArticleRepository(s)
        assert await repo.get_by_id(999999) is None
