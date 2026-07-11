from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from bot.database.models import ArticleAction, CheckStatus, QuestionStatus, Role
from bot.database.repositories.article_check_repository import ArticleCheckRepository
from bot.database.repositories.user_repository import UserRepository
from bot.services.article_check_service import ArticleCheckService
from bot.services.question_service import QuestionService, escalation_job
from bot.services.setting_service import SettingService
from tests.test_article_check_service import seed


async def _receiver(session):
    return await UserRepository(session).upsert(telegram_id=99, name="Эксперт",
                                                role=Role.PARTNER)


async def test_article_question_creates_taskquestion(session):   # тест 9
    inst, valya, _ = await seed(session, n_articles=2)
    receiver = await _receiver(session)
    await SettingService(session).set("questions.default_receiver_user_id",
                                      receiver.id, valya.id)
    svc = ArticleCheckService(session)
    s = await svc.start_check(inst, valya)
    item = (await ArticleCheckRepository(session).get_items_for_batch(s.id, 1, 15))[0]
    await svc.mark(item.id, 1, CheckStatus.QUESTION, valya)

    bot = AsyncMock()
    bot.send_message.return_value = SimpleNamespace(message_id=42,
                                                    chat=SimpleNamespace(id=99))
    qsvc = QuestionService(session, bot)
    q = await qsvc.ask(inst, valya, "Нужно ли снижать цену?", item=item)
    await session.commit()
    assert q.article_check_item_id == item.id and q.to_user_id == receiver.id
    assert q.status == QuestionStatus.SENT
    text = bot.send_message.await_args.kwargs["text"]
    assert "Вопрос по артикулу" in text and item.article_snapshot in text


async def test_general_question_without_item(session):           # тест 10
    inst, valya, _ = await seed(session)
    receiver = await _receiver(session)
    await SettingService(session).set("questions.default_receiver_user_id",
                                      receiver.id, valya.id)
    bot = AsyncMock()
    bot.send_message.return_value = SimpleNamespace(message_id=1,
                                                    chat=SimpleNamespace(id=99))
    q = await QuestionService(session, bot).ask(inst, valya, "Общий вопрос?")
    assert q.article_check_item_id is None
    assert "Вопрос по задаче" in bot.send_message.await_args.kwargs["text"]


async def test_delivery_failed_status(session):
    inst, valya, _ = await seed(session)
    receiver = await _receiver(session)
    await SettingService(session).set("questions.default_receiver_user_id",
                                      receiver.id, valya.id)
    bot = AsyncMock()
    bot.send_message.side_effect = RuntimeError("blocked")
    q = await QuestionService(session, bot).ask(inst, valya, "?")
    assert q.status == QuestionStatus.DELIVERY_FAILED
    assert "blocked" in q.last_delivery_error


async def test_answer_flow_and_escalation(session, session_factory):
    inst, valya, _ = await seed(session)
    receiver = await _receiver(session)
    await SettingService(session).set("questions.default_receiver_user_id",
                                      receiver.id, valya.id)
    bot = AsyncMock()
    bot.send_message.return_value = SimpleNamespace(message_id=1,
                                                    chat=SimpleNamespace(id=99))
    qsvc = QuestionService(session, bot)
    q = await qsvc.ask(inst, valya, "?")
    with pytest.raises(PermissionError):
        await qsvc.answer(q.id, valya, "сам себе")               # не адресат
    got = await qsvc.answer(q.id, receiver, "Снижай цену")
    assert got.status == QuestionStatus.ANSWERED and got.answer_text == "Снижай цену"


async def test_inactive_receiver_falls_back(session):
    inst, valya, _ = await seed(session)
    receiver = await _receiver(session)
    fallback = await UserRepository(session).upsert(telegram_id=98, name="Резерв",
                                                    role=Role.OWNER)
    settings = SettingService(session)
    await settings.set("questions.default_receiver_user_id", receiver.id, valya.id)
    await settings.set("questions.fallback_receiver_user_id", fallback.id, valya.id)
    await UserRepository(session).deactivate(receiver.id)
    got = await QuestionService(session, AsyncMock()).resolve_receiver(inst)
    assert got.id == fallback.id                                  # неактивный пропущен


async def test_route_by_category_prefers_matching_category(session):
    """resolve_receiver реально смотрит на категорию КОНКРЕТНОГО артикула
    (через историю ArticleAction), а не берёт первого попавшегося получателя
    из questions.route_by_category, если в нём есть хоть одна запись."""
    inst, valya, _ = await seed(session, n_articles=2)
    default_receiver = await _receiver(session)
    category_receiver = await UserRepository(session).upsert(
        telegram_id=97, name="По категории", role=Role.PARTNER)
    settings = SettingService(session)
    await settings.set("questions.default_receiver_user_id", default_receiver.id, valya.id)
    await settings.set("questions.route_by_category",
                       {"Реклама": category_receiver.id}, valya.id)

    svc = ArticleCheckService(session)
    s = await svc.start_check(inst, valya)
    items = await ArticleCheckRepository(session).get_items_for_batch(s.id, 1, 15)
    item_with_category, item_without_category = items[0], items[1]
    session.add(ArticleAction(
        article_check_item_id=item_with_category.id, task_instance_id=inst.id,
        user_id=valya.id, article=item_with_category.article_snapshot,
        category_name_snapshot="Реклама"))
    await session.commit()

    qsvc = QuestionService(session, AsyncMock())
    got_matching = await qsvc.resolve_receiver(inst, item_with_category)
    assert got_matching.id == category_receiver.id

    got_unmatched = await qsvc.resolve_receiver(inst, item_without_category)
    assert got_unmatched.id == default_receiver.id                # нет своей категории — default


async def test_escalation_job_escalates_unanswered_and_notifies(session, session_factory):
    inst, valya, owner = await seed(session)
    receiver = await _receiver(session)
    settings = SettingService(session)
    await settings.set("questions.default_receiver_user_id", receiver.id, valya.id)
    bot = AsyncMock()
    bot.send_message.return_value = SimpleNamespace(message_id=1,
                                                    chat=SimpleNamespace(id=99))
    q = await QuestionService(session, bot).ask(inst, valya, "?")
    await session.commit()

    await escalation_job(q.id, bot, session_factory)

    async with session_factory() as fresh_session:
        from bot.database.repositories.question_repository import QuestionRepository
        fresh = await QuestionRepository(fresh_session).get(q.id)
        assert fresh.status == QuestionStatus.ESCALATED
        assert fresh.escalated_at is not None
    # owner (без явного escalation_receiver_user_id) получает уведомление
    assert any(c.kwargs.get("chat_id") == owner.telegram_id
              for c in bot.send_message.await_args_list)


async def test_escalation_job_noop_if_already_answered(session, session_factory):
    inst, valya, _ = await seed(session)
    receiver = await _receiver(session)
    await SettingService(session).set("questions.default_receiver_user_id",
                                      receiver.id, valya.id)
    bot = AsyncMock()
    bot.send_message.return_value = SimpleNamespace(message_id=1,
                                                    chat=SimpleNamespace(id=99))
    qsvc = QuestionService(session, bot)
    q = await qsvc.ask(inst, valya, "?")
    await qsvc.answer(q.id, receiver, "Ответ")
    await session.commit()

    await escalation_job(q.id, bot, session_factory)

    async with session_factory() as fresh_session:
        from bot.database.repositories.question_repository import QuestionRepository
        fresh = await QuestionRepository(fresh_session).get(q.id)
        assert fresh.status == QuestionStatus.ANSWERED               # не переписано
