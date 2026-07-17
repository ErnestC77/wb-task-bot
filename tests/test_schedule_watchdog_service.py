"""Тесты watchdog-алерта о просроченном расписании (инцидент 2026-07-17)."""
from datetime import datetime, timedelta
from unittest.mock import AsyncMock

from bot.database.models import Role
from bot.database.repositories.task_repository import TaskRepository
from bot.database.repositories.user_repository import UserRepository
from bot.services.schedule_watchdog_service import schedule_watchdog_job
from bot.services.setting_service import SettingService


async def _make_config(session_factory, next_run_at, is_active=True) -> int:
    async with session_factory() as s:
        await UserRepository(s).upsert(telegram_id=99, name="Хозяин", role=Role.OWNER)
        cfg = await TaskRepository(s).upsert_config(dict(
            external_task_id="watched", title="Сверить остатки", scenario="simple",
            schedule_type="every_n_days", schedule_interval=2, is_active=is_active,
            next_run_at=next_run_at))
        await s.commit()
        return cfg.id


async def test_disabled_sends_nothing_even_when_overdue(session_factory):
    await _make_config(session_factory, datetime.utcnow() - timedelta(hours=2))
    async with session_factory() as s:
        await SettingService(s).set("schedule_watchdog.enabled", False, actor_user_id=None)
        await s.commit()
    bot = AsyncMock()
    await schedule_watchdog_job(bot, session_factory)
    bot.send_message.assert_not_awaited()


async def test_not_overdue_within_grace_sends_nothing(session_factory):
    # grace по умолчанию 30 мин — 10 мин просрочки ещё не повод для алерта
    await _make_config(session_factory, datetime.utcnow() - timedelta(minutes=10))
    bot = AsyncMock()
    await schedule_watchdog_job(bot, session_factory)
    bot.send_message.assert_not_awaited()


async def test_overdue_past_grace_alerts_owner(session_factory):
    await _make_config(session_factory, datetime.utcnow() - timedelta(hours=2))
    bot = AsyncMock()
    await schedule_watchdog_job(bot, session_factory)
    bot.send_message.assert_awaited_once()
    call = bot.send_message.await_args
    assert call.kwargs["chat_id"] == 99
    assert "Сверить остатки" in call.kwargs["text"]


async def test_inactive_config_not_alerted(session_factory):
    await _make_config(session_factory, datetime.utcnow() - timedelta(hours=2),
                       is_active=False)
    bot = AsyncMock()
    await schedule_watchdog_job(bot, session_factory)
    bot.send_message.assert_not_awaited()


async def test_grace_minutes_setting_is_dynamic(session_factory):
    await _make_config(session_factory, datetime.utcnow() - timedelta(minutes=45))
    async with session_factory() as s:
        await SettingService(s).set("schedule_watchdog.grace_minutes", 60,
                                    actor_user_id=None)
        await s.commit()
    bot = AsyncMock()
    await schedule_watchdog_job(bot, session_factory)
    bot.send_message.assert_not_awaited()          # 45 мин < новый grace 60 мин


async def test_targets_add_partner_recipients(session_factory):
    await _make_config(session_factory, datetime.utcnow() - timedelta(hours=2))
    async with session_factory() as s:
        await UserRepository(s).upsert(telegram_id=77, name="Партнёр", role=Role.PARTNER)
        await SettingService(s).set("schedule_watchdog.targets", ["owner", "partner"],
                                    actor_user_id=None)
        await s.commit()
    bot = AsyncMock()
    await schedule_watchdog_job(bot, session_factory)
    chats = sorted(c.kwargs["chat_id"] for c in bot.send_message.await_args_list)
    assert chats == [77, 99]


async def test_send_failure_does_not_break_job(session_factory):
    await _make_config(session_factory, datetime.utcnow() - timedelta(hours=2))
    bot = AsyncMock()
    bot.send_message.side_effect = RuntimeError("bot was blocked by the user")
    await schedule_watchdog_job(bot, session_factory)      # не должен упасть наружу
