from datetime import datetime

from bot.database.models import Article, CheckStatus, Role
from bot.database.repositories.article_check_repository import ArticleCheckRepository
from bot.database.repositories.task_repository import TaskRepository
from bot.database.repositories.user_repository import UserRepository


async def make_session_with_articles(session, n_articles, batch_size=15):
    user = await UserRepository(session).upsert(telegram_id=1, name="Валя", role=Role.MANAGER_WB)
    repo = TaskRepository(session)
    cfg = await repo.upsert_config(dict(external_task_id="chk", title="Проверка",
                                        scenario="article_check", schedule_type="every_n_days",
                                        schedule_interval=2, responsible_user_id=user.id,
                                        is_active=True))
    inst = await repo.create_instance_idempotent(
        cfg, datetime(2026, 7, 10, 9), datetime(2026, 7, 10, 12),
        dict(title_snapshot="Проверка", article_batch_size_snapshot=batch_size,
             scenario_snapshot="article_check"))
    articles = [Article(article=f"{10000000 + i}", product_name=f"Товар {i}", sort_order=i)
                for i in range(n_articles)]
    session.add_all(articles)
    await session.flush()
    chk = ArticleCheckRepository(session)
    check_session = await chk.create_session(inst.id, user.id, batch_size, articles)
    await session.commit()
    return chk, check_session, inst, user


async def test_batches_37_articles_15_15_7(session):        # тест 1
    chk, s, _, _ = await make_session_with_articles(session, 37)
    assert s.total_articles == 37
    assert len(await chk.get_items_for_batch(s.id, 1, 15)) == 15
    assert len(await chk.get_items_for_batch(s.id, 2, 15)) == 15
    assert len(await chk.get_items_for_batch(s.id, 3, 15)) == 7


async def test_batches_90_articles_6_batches(session):      # тест 2
    chk, s, _, _ = await make_session_with_articles(session, 90)
    import math
    assert math.ceil(s.total_articles / s.batch_size) == 6
    assert len(await chk.get_items_for_batch(s.id, 6, 15)) == 15
    assert await chk.get_items_for_batch(s.id, 7, 15) == []


async def test_second_session_for_same_instance_rejected(session):   # тест 13
    chk, s, inst, user = await make_session_with_articles(session, 5)
    dup = await chk.create_session(inst.id, user.id, 15, [])
    await session.commit()
    assert dup is None


async def test_mark_item_optimistic_lock(session):
    chk, s, _, user = await make_session_with_articles(session, 5)
    item = (await chk.get_items_for_batch(s.id, 1, 15))[0]
    # Захватываем version ОДИН раз до обоих вызовов — второй вызов симулирует
    # второго пользователя, нажавшего кнопку со старым callback_data, пока
    # первый уже успешно обновил item (а не повторное чтение уже
    # смутировавшего в памяти item.version).
    expected_version = item.version
    ok = await chk.mark_item(item.id, expected_version, CheckStatus.ACTION_REQUIRED, user.id)
    stale = await chk.mark_item(item.id, expected_version, CheckStatus.CHECKED_NO_ACTION, user.id)
    await session.commit()
    assert ok is True and stale is False     # устаревшая version отклонена
    counts = await chk.count_by_status(s.id)
    assert counts["action_required"] == 1 and counts["pending"] == 4


async def test_mark_item_visible_in_same_session(session):
    # Регрессия: synchronize_session=False в mark_item приводил к тому, что
    # get_item()/get_items_for_batch()/pending_in_batch() в той же сессии
    # видели устаревшие данные из identity map после успешного mark_item.
    chk, s, _, user = await make_session_with_articles(session, 5)
    items = await chk.get_items_for_batch(s.id, 1, 15)
    target = items[0]
    original_version = target.version

    assert await chk.pending_in_batch(s.id, 1, 15) == 5

    ok = await chk.mark_item(target.id, original_version, CheckStatus.ACTION_REQUIRED, user.id)
    await session.commit()
    assert ok is True

    refreshed = await chk.get_item(target.id)
    assert refreshed.check_status == CheckStatus.ACTION_REQUIRED
    assert refreshed.version == original_version + 1

    assert await chk.pending_in_batch(s.id, 1, 15) == 4
