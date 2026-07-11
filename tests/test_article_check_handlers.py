from datetime import datetime
from unittest.mock import AsyncMock

import pytest

from bot.database.models import Article, ArticleCheckSession, CheckStatus, Role
from bot.database.repositories.article_check_repository import ArticleCheckRepository
from bot.database.repositories.task_repository import TaskRepository
from bot.database.repositories.user_repository import UserRepository
from bot.keyboards.article_check_keyboards import ChkCb, batch_keyboard, render_batch
from bot.services.article_check_service import ArticleCheckService
from bot.services.setting_service import SettingService
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


def _denied(callback: AsyncMock) -> None:
    """Общая проверка: ровно один answer(show_alert=True) с отказом."""
    callback.answer.assert_awaited_once()
    args, kwargs = callback.answer.call_args
    assert kwargs.get("show_alert") is True
    text = args[0] if args else kwargs.get("text", "")
    assert any(w in text.lower() for w in ("прав", "недоступ"))


async def test_open_item_denies_foreign_registered_user(session):    # находка 1
    inst, valya, owner = await seed(session, n_articles=3)
    svc = ArticleCheckService(session)
    s = await svc.start_check(inst, valya)
    await session.commit()
    item = (await ArticleCheckRepository(session).get_items_for_batch(s.id, 1, 15))[0]

    from bot.handlers.article_check import handle_open_item
    callback = AsyncMock()
    callback.from_user.id = owner.telegram_id      # зарегистрирован, но не ответственный
    cb_data = ChkCb(a="open", s=s.id, it=item.id)
    await handle_open_item(callback, cb_data, session)
    _denied(callback)
    callback.message.edit_text.assert_not_awaited()


async def test_open_item_denies_unregistered_user(session):          # находка 1
    inst, valya, _ = await seed(session, n_articles=3)
    svc = ArticleCheckService(session)
    s = await svc.start_check(inst, valya)
    await session.commit()
    item = (await ArticleCheckRepository(session).get_items_for_batch(s.id, 1, 15))[0]

    from bot.handlers.article_check import handle_open_item
    callback = AsyncMock()
    callback.from_user.id = 999999999               # не зарегистрирован
    cb_data = ChkCb(a="open", s=s.id, it=item.id)
    await handle_open_item(callback, cb_data, session)
    _denied(callback)
    callback.message.edit_text.assert_not_awaited()


async def test_finish_batch_denies_foreign_registered_user(session):  # находка 2
    inst, valya, owner = await seed(session, n_articles=3)
    svc = ArticleCheckService(session)
    s = await svc.start_check(inst, valya)
    await session.commit()

    from bot.handlers.article_check import handle_finish_batch
    callback = AsyncMock()
    callback.from_user.id = owner.telegram_id
    cb_data = ChkCb(a="fin", s=s.id, b=1)
    await handle_finish_batch(callback, cb_data, session)
    _denied(callback)
    callback.message.edit_text.assert_not_awaited()
    fresh = await session.get(ArticleCheckSession, s.id)
    assert fresh.current_batch == 1                 # ничего не продвинулось


async def test_finish_batch_denies_unregistered_user(session):        # находка 2
    inst, valya, _ = await seed(session, n_articles=3)
    svc = ArticleCheckService(session)
    s = await svc.start_check(inst, valya)
    await session.commit()

    from bot.handlers.article_check import handle_finish_batch
    callback = AsyncMock()
    callback.from_user.id = 999999999
    cb_data = ChkCb(a="fin", s=s.id, b=1)
    await handle_finish_batch(callback, cb_data, session)
    _denied(callback)


async def test_mark_unregistered_user_no_exception(session):          # находка 3
    inst, valya, _ = await seed(session, n_articles=3)
    svc = ArticleCheckService(session)
    s = await svc.start_check(inst, valya)
    await session.commit()
    item = (await ArticleCheckRepository(session).get_items_for_batch(s.id, 1, 15))[0]

    from bot.handlers.article_check import handle_mark
    callback = AsyncMock()
    callback.from_user.id = 999999999                # не зарегистрирован → actor is None
    cb_data = ChkCb(a="mark", s=s.id, it=item.id, v=item.version, st="ok")
    await handle_mark(callback, cb_data, session)     # не должно бросить AttributeError
    _denied(callback)
    fresh = await svc.repo.get_item(item.id)
    assert fresh.check_status == CheckStatus.PENDING  # ничего не изменилось


async def test_nav_denies_foreign_registered_user(session):           # находка 4а
    inst, valya, owner = await seed(session, n_articles=40)
    svc = ArticleCheckService(session)
    s = await svc.start_check(inst, valya)
    await session.commit()

    from bot.handlers.article_check import handle_nav
    callback = AsyncMock()
    callback.from_user.id = owner.telegram_id
    cb_data = ChkCb(a="nav", s=s.id, b=2)
    await handle_nav(callback, cb_data, session)
    _denied(callback)
    callback.message.edit_text.assert_not_awaited()
    fresh = await session.get(ArticleCheckSession, s.id)
    assert fresh.current_batch == 1


async def test_nav_blocks_backward_jump_when_disallowed(session):     # находка 4б
    inst, valya, _ = await seed(session, n_articles=40)
    svc = ArticleCheckService(session)
    s = await svc.start_check(inst, valya)
    await session.commit()
    await svc.next_batch(s.id)                       # текущая пачка = 2
    await session.commit()
    await SettingService(session).set("article_check.allow_prev_batch", False, valya.id)

    from bot.handlers.article_check import handle_nav
    callback = AsyncMock()
    callback.from_user.id = valya.telegram_id
    cb_data = ChkCb(a="nav", s=s.id, b=1)             # прыжок назад на пачку 1
    await handle_nav(callback, cb_data, session)
    callback.answer.assert_awaited_once()
    args, kwargs = callback.answer.call_args
    assert kwargs.get("show_alert") is True
    text = args[0] if args else kwargs.get("text", "")
    assert "предыдущ" in text.lower() or "запрещ" in text.lower()
    callback.message.edit_text.assert_not_awaited()
    fresh = await session.get(ArticleCheckSession, s.id)
    assert fresh.current_batch == 2                  # не сдвинулось назад


async def test_nav_allows_forward_jump_even_when_prev_disallowed(session):  # находка 4б, контроль
    inst, valya, _ = await seed(session, n_articles=40)
    svc = ArticleCheckService(session)
    s = await svc.start_check(inst, valya)
    await session.commit()
    await SettingService(session).set("article_check.allow_prev_batch", False, valya.id)

    from bot.handlers.article_check import handle_nav
    callback = AsyncMock()
    callback.from_user.id = valya.telegram_id
    cb_data = ChkCb(a="nav", s=s.id, b=2)             # вперёд — должно быть разрешено
    await handle_nav(callback, cb_data, session)
    callback.message.edit_text.assert_awaited_once()
    fresh = await session.get(ArticleCheckSession, s.id)
    assert fresh.current_batch == 2


async def test_mark_rerender_uses_real_item_session_not_spoofed_callback(session, monkeypatch):
    """находка 5: callback_data.s подделан на чужую (но существующую) сессию —
    рендер должен идти по item.check_session_id, а не по callback_data.s."""
    inst, valya, _ = await seed(session, n_articles=3)
    svc = ArticleCheckService(session)
    s_a = await svc.start_check(inst, valya)
    await session.commit()

    tasks = TaskRepository(session)
    other_cfg = await tasks.upsert_config(dict(
        external_task_id="articles_check_other", title="Проверка B",
        scenario="article_check", schedule_type="every_n_days", schedule_interval=2,
        responsible_user_id=valya.id, need_approval=True, is_active=True))
    other_inst = await tasks.create_instance_idempotent(
        other_cfg, datetime(2026, 7, 10, 9), datetime(2026, 7, 10, 12),
        dict(title_snapshot="Проверка B", scenario_snapshot="article_check",
             article_batch_size_snapshot=15, need_approval_snapshot=True,
             approval_timeout_hours_snapshot=24, responsible_name_snapshot="Валя"))
    await session.commit()
    s_b = await svc.start_check(other_inst, valya)
    await session.commit()
    assert s_a.id != s_b.id

    item = (await ArticleCheckRepository(session).get_items_for_batch(s_a.id, 1, 15))[0]

    captured = {}
    original = ArticleCheckService.get_batch_view

    async def spy(self, session_id, batch):
        captured["session_id"] = session_id
        return await original(self, session_id, batch)

    monkeypatch.setattr(ArticleCheckService, "get_batch_view", spy)

    from bot.handlers.article_check import handle_mark
    callback = AsyncMock()
    callback.from_user.id = valya.telegram_id
    cb_data = ChkCb(a="mark", s=s_b.id, it=item.id, v=item.version, st="ok")  # s подделан
    await handle_mark(callback, cb_data, session)

    assert captured["session_id"] == s_a.id          # рендер — по реальной сессии item'а
