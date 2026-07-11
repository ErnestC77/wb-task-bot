"""Task 38 Step 7: TopicService.send_test_message / DeliveryService.send_private
на реальной PostgreSQL-сессии — с AsyncMock вместо реального bot.send_message
(сеть отключена, проверяем совместимость ORM-запросов с диалектом Postgres,
не сетевой вызов Telegram)."""
from unittest.mock import AsyncMock

import pytest
from types import SimpleNamespace

from bot.database.models import Role, Topic
from bot.database.repositories.user_repository import UserRepository
from bot.services.delivery_service import DeliveryService
from bot.services.topic_service import TopicService

pytestmark = pytest.mark.pg


async def test_send_test_message_resolves_thread_id_on_postgres(pg_session):
    pg_session.add(Topic(topic_key="goods", topic_name="Товары",
                         message_thread_id=42, is_active=True))
    await pg_session.commit()

    bot = AsyncMock()
    bot.send_message.return_value = SimpleNamespace(message_id=1)
    ok = await TopicService(pg_session, bot).send_test_message("goods")

    assert ok is True
    bot.send_message.assert_awaited_once()
    assert bot.send_message.await_args.kwargs["message_thread_id"] == 42
    bot.delete_message.assert_awaited_once()


async def test_send_test_message_inactive_topic_uses_no_thread_id(pg_session):
    pg_session.add(Topic(topic_key="archived", topic_name="Архив",
                         message_thread_id=99, is_active=False))
    await pg_session.commit()

    bot = AsyncMock()
    bot.send_message.return_value = SimpleNamespace(message_id=1)
    ok = await TopicService(pg_session, bot).send_test_message("archived")

    assert ok is True
    assert bot.send_message.await_args.kwargs["message_thread_id"] is None


async def test_send_test_message_returns_false_on_send_failure(pg_session):
    bot = AsyncMock()
    bot.send_message.side_effect = Exception("недоступно")
    ok = await TopicService(pg_session, bot).send_test_message("goods")
    assert ok is False


async def test_send_private_message_on_postgres(pg_session):
    owner = await UserRepository(pg_session).upsert(telegram_id=555, name="O", role=Role.OWNER)
    await pg_session.commit()

    bot = AsyncMock()
    bot.send_message.return_value = SimpleNamespace(message_id=7)
    ok, msg_id = await DeliveryService(pg_session, bot).send_private(
        owner.telegram_id, "тест")

    assert ok is True and msg_id == "7"
    bot.send_message.assert_awaited_once_with(chat_id=owner.telegram_id, text="тест",
                                              reply_markup=None)


async def test_send_private_message_failure_on_postgres(pg_session):
    bot = AsyncMock()
    bot.send_message.side_effect = Exception("сеть недоступна")
    ok, err = await DeliveryService(pg_session, bot).send_private(999, "тест")
    assert ok is False and err is not None
