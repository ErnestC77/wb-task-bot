import time
from contextlib import asynccontextmanager

from bot.services.heartbeat_service import heartbeat_job


async def test_heartbeat_job_writes_fresh_timestamp(session_factory, tmp_path):
    path = tmp_path / "heartbeat"
    before = time.time()
    await heartbeat_job(session_factory, path=str(path))
    written = float(path.read_text())
    assert before <= written <= time.time()


async def test_heartbeat_job_does_not_write_when_db_unreachable(tmp_path):
    path = tmp_path / "heartbeat"

    @asynccontextmanager
    async def broken_session():
        raise RuntimeError("db unreachable")
        yield  # pragma: no cover — делает функцию генератором для asynccontextmanager

    def broken_factory():
        return broken_session()

    try:
        await heartbeat_job(broken_factory, path=str(path))
    except RuntimeError:
        pass
    assert not path.exists()
