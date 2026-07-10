from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

from bot.database.models import DeliveryStatus, Role
from bot.database.repositories.delivery_repository import DeliveryRepository
from bot.services.delivery_service import DeliveryService
from bot.services.setting_service import SettingService
from tests.test_task_service import make_config
from bot.services.task_service import TaskService


async def _instance(session):
    cfg, valya = await make_config(session)
    inst = await TaskService(session).create_instance_for(cfg, datetime(2026, 7, 10, 9))
    await session.commit()
    return inst


async def test_success_marks_sent_and_stores_message_id(session):
    inst = await _instance(session)
    bot = AsyncMock()
    bot.send_message.return_value = SimpleNamespace(message_id=777, chat=SimpleNamespace(id=-100))
    svc = DeliveryService(session, bot)
    assert await svc.send_task_message(inst) is True
    await session.commit()
    assert inst.delivery_status == DeliveryStatus.SENT
    assert inst.telegram_message_id == 777 and inst.message_sent_at is not None
    attempts = await DeliveryRepository(session).attempts_for("task_instance", inst.id)
    assert len(attempts) == 1 and attempts[0].status == "sent"


async def test_failure_schedules_retry_then_abandons(session):
    inst = await _instance(session)
    bot = AsyncMock()
    bot.send_message.side_effect = RuntimeError("Telegram down")
    svc = DeliveryService(session, bot)
    for expected_attempts in (1, 2, 3):
        assert await svc.send_task_message(inst) is False
        assert inst.delivery_attempts == expected_attempts
    await session.commit()
    # telegram_retry_count по умолчанию 3 — после третьей неудачи abandoned
    assert inst.delivery_status == DeliveryStatus.ABANDONED
    assert inst.telegram_message_id is None            # без message_id — не доставлено
    assert "Telegram down" in inst.last_delivery_error


async def test_retry_after_failure_has_next_retry_at(session):
    inst = await _instance(session)
    bot = AsyncMock()
    bot.send_message.side_effect = RuntimeError("boom")
    svc = DeliveryService(session, bot)
    await svc.send_task_message(inst)
    assert inst.delivery_status == DeliveryStatus.RETRYING
    assert inst.next_retry_at is not None


async def test_failure_with_empty_retry_intervals_uses_fallback_delay(session):
    inst = await _instance(session)
    settings = SettingService(session)
    # general.telegram_retry_intervals допустимо пуст — SettingService не проверяет
    # минимальную длину списка. Раньше это роняло send_task_message необработанным
    # IndexError (intervals[-1] на пустом списке).
    await settings.set("general.telegram_retry_intervals", [], actor_user_id=None)
    bot = AsyncMock()
    bot.send_message.side_effect = RuntimeError("Telegram down")
    svc = DeliveryService(session, bot)
    result = await svc.send_task_message(inst)          # не должно бросать IndexError
    await session.commit()
    assert result is False
    assert inst.delivery_status == DeliveryStatus.RETRYING
    assert inst.next_retry_at is not None


async def test_send_task_message_on_already_sent_instance_does_not_duplicate(session):
    inst = await _instance(session)
    bot = AsyncMock()
    bot.send_message.return_value = SimpleNamespace(message_id=777, chat=SimpleNamespace(id=-100))
    svc = DeliveryService(session, bot)
    assert await svc.send_task_message(inst) is True
    await session.commit()
    assert bot.send_message.call_count == 1
    assert inst.telegram_message_id == 777

    # повторный вызов на уже SENT-инстансе не должен слать ещё одно сообщение
    # в Telegram и не должен перезаписывать telegram_message_id
    result = await svc.send_task_message(inst)
    await session.commit()
    assert result is True
    assert bot.send_message.call_count == 1             # без дубля
    assert inst.telegram_message_id == 777
    attempts = await DeliveryRepository(session).attempts_for("task_instance", inst.id)
    assert len(attempts) == 1                            # без второй "sent"-записи
