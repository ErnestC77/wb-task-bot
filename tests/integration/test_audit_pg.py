"""Task 38 Step 11: записи AdminAuditLog корректно сортируются ORDER BY id DESC
с пагинацией на большом объёме — вставляем 250 записей, проверяем страницы
1..25 (по 10) без дублей/пропусков, на реальном Postgres."""
import pytest

from bot.database.repositories.audit_repository import AuditRepository

pytestmark = pytest.mark.pg


async def test_audit_pagination_no_gaps_or_duplicates_on_250_rows(pg_session):
    repo = AuditRepository(pg_session)
    for i in range(250):
        await repo.add(actor_user_id=None, action=f"test.action.{i}", result="ok")
    await pg_session.commit()

    seen_ids: list[int] = []
    page_size = 10
    for page in range(1, 26):
        rows = await repo.list_page(page=page, page_size=page_size)
        assert len(rows) == page_size
        seen_ids.extend(r.id for r in rows)

    assert len(seen_ids) == 250
    assert len(set(seen_ids)) == 250                       # без дублей
    # ORDER BY id DESC: страницы идут строго по убыванию, без пропусков
    assert seen_ids == sorted(seen_ids, reverse=True)
    assert sorted(seen_ids) == list(range(min(seen_ids), min(seen_ids) + 250))


async def test_audit_pagination_beyond_last_page_is_empty(pg_session):
    repo = AuditRepository(pg_session)
    for i in range(5):
        await repo.add(actor_user_id=None, action=f"few.{i}", result="ok")
    await pg_session.commit()

    rows = await repo.list_page(page=2, page_size=10)
    assert rows == []
