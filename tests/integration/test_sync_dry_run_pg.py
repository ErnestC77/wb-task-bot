"""Task 38 Step 9: GoogleSheetsService.sync_all(dry_run=True) на реальном
PostgreSQL — убеждаемся, что `session.begin_nested()` (SAVEPOINT) откатывается
корректно на реальном диалекте, не оставляя ни одной строки в бизнес-таблицах,
но при этом фиксирует audit-запись о запуске (см. docstring
google_sheets_service.py: dry_run коммитит audit отдельно от отката бизнес-
данных). В SQLite вложенные транзакции эмулируются иначе — этот тест реально
использует PostgreSQL SAVEPOINT."""
import pytest
from sqlalchemy import select

from bot.database.models import AdminAuditLog, Article, Role, Topic
from bot.database.repositories.user_repository import UserRepository
from bot.services.google_sheets_service import GoogleSheetsService

pytestmark = pytest.mark.pg


async def test_sync_all_dry_run_rolls_back_savepoint_on_postgres(pg_session):
    owner = await UserRepository(pg_session).upsert(telegram_id=1, name="O", role=Role.OWNER)
    await pg_session.commit()
    owner_id = owner.id                          # захватить ДО sync_all — см. ниже

    svc = GoogleSheetsService(pg_session, client=None)
    # sync_all делает несколько begin_nested()+rollback (dry-run по каждому
    # листу) — ЛЮБОЙ rollback (в отличие от commit, независимо от
    # expire_on_commit) истекает identity map всей сессии, поэтому `owner`
    # (загруженный ДО вызова) после этого небезопасно трогать напрямую: синхронный
    # доступ к атрибуту истёкшего объекта попытался бы неявный lazy-reload вне
    # await-контекста и упал бы MissingGreenlet на реальном asyncpg-диалекте
    # (см. также docstring google_sheets_service.py про rollback-семантику).
    results = await svc.sync_all(dry_run=True, actor_user_id=owner_id)
    await pg_session.commit()

    assert "articles" in results and "users" in results
    assert (await pg_session.scalar(select(Article).limit(1))) is None
    assert (await pg_session.scalar(select(Topic).limit(1))) is None
    # созданный ранее owner не деактивирован откатом SAVEPOINT
    refreshed = await UserRepository(pg_session).get_by_id(owner_id)
    assert refreshed.is_active is True

    logs = list(await pg_session.scalars(select(AdminAuditLog)))
    assert any(l.action == "sync.run" and l.result == "dry_run" for l in logs)


async def test_sync_articles_dry_run_does_not_persist_on_postgres(pg_session):
    from bot.database.repositories.article_repository import ArticleRepository

    svc = GoogleSheetsService(pg_session, client=None)
    rows = [{"article": "111", "product_name": "Товар", "sort_order": "1", "active": "1"}]
    report = await svc.sync_articles(rows, dry_run=True)
    await pg_session.commit()

    assert report.added == 1                              # отчёт наполнен...
    assert await ArticleRepository(pg_session).get_by_article("111") is None  # ...но не сохранено
