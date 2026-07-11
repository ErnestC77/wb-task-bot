import pytest
import pytest_asyncio
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from bot.database.db import Base
import bot.database.models  # noqa: F401 — регистрирует таблицы в metadata
from bot.services import setting_service


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    setting_service.invalidate()
    yield
    setting_service.invalidate()


@pytest_asyncio.fixture
async def session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)

    # Task 22: стандартный SQLAlchemy-фикс для pysqlite/aiosqlite — без него
    # pysqlite сам управляет транзакциями «за спиной» SQLAlchemy, из-за чего
    # session.rollback()/close() ненадёжно откатывает уже flush()-нутые
    # изменения ПОСЛЕ того, как более ранний SAVEPOINT (begin_nested) был
    # штатно освобождён (не откачен). Обнаружено тестом на откат ВСЕЙ
    # транзакции sync_all при критической ошибке на одном из листов —
    # без фикса откат срабатывал только для SAVEPOINT, в котором произошла
    # сама ошибка, а не для всего запуска. См. документацию SQLAlchemy:
    # "Serializable isolation / Savepoints" для pysqlite.
    @event.listens_for(engine.sync_engine, "connect")
    def _do_connect(dbapi_connection, connection_record):  # noqa: ANN001, ARG001
        dbapi_connection.isolation_level = None

    @event.listens_for(engine.sync_engine, "begin")
    def _do_begin(conn):  # noqa: ANN001
        conn.exec_driver_sql("BEGIN")

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest_asyncio.fixture
async def session(session_factory):
    async with session_factory() as s:
        yield s
