from functools import lru_cache

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.database.db import create_engine_and_factory
from webadmin.config import get_webadmin_settings


@lru_cache
def get_session_factory() -> async_sessionmaker[AsyncSession]:
    settings = get_webadmin_settings()
    _, factory = create_engine_and_factory(settings.database_url)
    return factory


async def get_db():
    factory = get_session_factory()
    async with factory() as session:
        yield session
