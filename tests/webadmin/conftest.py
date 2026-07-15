import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from webadmin.config import get_webadmin_settings


@pytest.fixture(autouse=True)
def _webadmin_env(monkeypatch):
    """WebAdminSettings требует все поля без дефолта — подставляем тестовые
    значения через env, не трогая реальный .env разработчика."""
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    monkeypatch.setenv("WEBADMIN_PASSWORD", "test-staff-pw")
    monkeypatch.setenv("CLIENT_PASSWORD", "test-client-pw")
    monkeypatch.setenv("WEBADMIN_SECRET_KEY", "test-secret-key")
    get_webadmin_settings.cache_clear()
    yield
    get_webadmin_settings.cache_clear()


@pytest_asyncio.fixture
async def app(session_factory):
    from webadmin.deps import get_db
    from webadmin.main import create_app

    application = create_app()

    async def _override_get_db():
        async with session_factory() as session:
            yield session

    application.dependency_overrides[get_db] = _override_get_db
    return application


@pytest_asyncio.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
