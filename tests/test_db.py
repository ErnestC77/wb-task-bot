from sqlalchemy import text

from bot.database.db import Base, create_engine_and_factory


async def test_engine_and_session_work():
    engine, factory = create_engine_and_factory("sqlite+aiosqlite:///:memory:")
    async with factory() as session:
        assert (await session.execute(text("SELECT 1"))).scalar() == 1
    await engine.dispose()


def test_base_is_declarative():
    assert hasattr(Base, "metadata")
