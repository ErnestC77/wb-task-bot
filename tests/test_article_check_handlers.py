from datetime import datetime
from unittest.mock import AsyncMock

import pytest

from bot.database.models import Article, CheckStatus, Role
from bot.database.repositories.article_check_repository import ArticleCheckRepository
from bot.keyboards.article_check_keyboards import ChkCb, batch_keyboard, render_batch
from bot.services.article_check_service import ArticleCheckService
from tests.test_article_check_service import seed


async def test_render_batch_escapes_html(session):              # тест 16
    inst, valya, _ = await seed(session, n_articles=1)
    # артикул с «опасным» названием
    session.add(Article(article="55555555", product_name="<script>alert(1)</script>"))
    await session.commit()
    svc = ArticleCheckService(session)
    s = await svc.start_check(inst, valya)
    view = await svc.get_batch_view(s.id, 1)
    text = render_batch(view, show_names=True)
    assert "<script>" not in text and "&lt;script&gt;" in text
    assert "Пачка 1 из 1" in text and "Проверено: 0 из 2" in text


async def test_fake_item_id_rejected(session):                  # тест 15
    inst, valya, _ = await seed(session, n_articles=3)
    svc = ArticleCheckService(session)
    await svc.start_check(inst, valya)
    with pytest.raises(ValueError):
        await svc.mark(999999, 1, CheckStatus.CHECKED_NO_ACTION, valya)


async def test_navigation_edits_single_message(session):        # тест 20
    """Handler навигации должен вызывать edit_message_text, а не send_message."""
    inst, valya, _ = await seed(session, n_articles=40)
    svc = ArticleCheckService(session)
    s = await svc.start_check(inst, valya)
    await session.commit()

    from bot.handlers.article_check import handle_nav
    callback = AsyncMock()
    callback.from_user.id = valya.telegram_id
    callback.message.message_id = 555
    cb_data = ChkCb(a="nav", s=s.id, b=2)
    await handle_nav(callback, cb_data, session)
    callback.message.edit_text.assert_awaited_once()      # редактирование
    callback.message.answer.assert_not_awaited()          # НЕ новое сообщение
    chk = ArticleCheckRepository(session)
    assert (await svc.get_batch_view(s.id, 2)).batch == 2


def test_batch_keyboard_layout():
    from types import SimpleNamespace
    items = [SimpleNamespace(id=i, article_snapshot=f"1000000{i}", version=1,
                             check_status=CheckStatus.PENDING, product_name_snapshot=None,
                             sort_order=i) for i in range(3)]
    view = SimpleNamespace(session=SimpleNamespace(id=1), items=items, batch=2,
                           total_batches=3, checked=15, total=37)
    kb = batch_keyboard(view, allow_prev=True)
    texts = [b.text for row in kb.inline_keyboard for b in row]
    assert any("Пред" in t for t in texts) and any("След" in t for t in texts)
    assert any("Завершить пачку" in t for t in texts)
    assert any("Общий вопрос" in t for t in texts)
