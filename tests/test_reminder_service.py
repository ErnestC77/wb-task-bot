from datetime import datetime
from unittest.mock import AsyncMock

from bot.database.models import Role, TaskStatus
from bot.database.repositories.task_repository import TaskRepository
from bot.database.repositories.user_repository import UserRepository
from bot.services.reminder_service import not_taken_reminder_job
from bot.services.setting_service import SettingService


async def seed_instance(session_factory, status=TaskStatus.CREATED):
    async with session_factory() as s:
        user = await UserRepository(s).upsert(telegram_id=10, name="Валя",
                                              role=Role.MANAGER_WB)
        repo = TaskRepository(s)
        cfg = await repo.upsert_config(dict(
            external_task_id="t", title="Проверка", scenario="article_check",
            schedule_type="every_n_days", schedule_interval=2,
            responsible_user_id=user.id, is_active=True))
        inst = await repo.create_instance_idempotent(
            cfg, datetime(2026, 7, 10, 9), datetime(2026, 7, 10, 12),
            dict(title_snapshot="Проверка", scenario_snapshot="article_check",
                 topic_snapshot=42, responsible_telegram_id_snapshot=10,
                 responsible_name_snapshot="Валя"))
        if status != TaskStatus.CREATED:
            await repo.transition_status(inst.id, [TaskStatus.CREATED], status, None, "x")
        await s.commit()
        return inst.id


async def test_sends_one_message_when_still_created(session_factory):
    inst_id = await seed_instance(session_factory)
    async with session_factory() as s:
        await SettingService(s).set("reminders.not_taken_after_hours", 12,
                                    actor_user_id=None)
        await s.commit()
    bot = AsyncMock()
    await not_taken_reminder_job(inst_id, bot, session_factory)
    bot.send_message.assert_awaited_once()
    kwargs = bot.send_message.await_args.kwargs
    assert kwargs["message_thread_id"] == 42
    assert "Проверка" in kwargs["text"]
    assert "12" in kwargs["text"]              # использует реальное значение настройки
    assert "не взята в работу" in kwargs["text"]


async def test_uses_custom_setting_value_in_text(session_factory):
    inst_id = await seed_instance(session_factory)
    async with session_factory() as s:
        await SettingService(s).set("reminders.not_taken_after_hours", 6,
                                    actor_user_id=None)
        await s.commit()
    bot = AsyncMock()
    await not_taken_reminder_job(inst_id, bot, session_factory)
    text = bot.send_message.await_args.kwargs["text"]
    assert "6" in text
    assert "12" not in text


async def test_skipped_when_already_in_progress(session_factory):
    inst_id = await seed_instance(session_factory, TaskStatus.IN_PROGRESS)
    bot = AsyncMock()
    await not_taken_reminder_job(inst_id, bot, session_factory)
    bot.send_message.assert_not_awaited()


async def test_skipped_when_completed(session_factory):
    inst_id = await seed_instance(session_factory, TaskStatus.COMPLETED)
    bot = AsyncMock()
    await not_taken_reminder_job(inst_id, bot, session_factory)
    bot.send_message.assert_not_awaited()


async def test_skipped_when_instance_missing(session_factory):
    bot = AsyncMock()
    await not_taken_reminder_job(999999, bot, session_factory)
    bot.send_message.assert_not_awaited()


async def test_send_failure_does_not_raise(session_factory):
    inst_id = await seed_instance(session_factory)
    bot = AsyncMock()
    bot.send_message.side_effect = RuntimeError("bot was blocked by the user")
    await not_taken_reminder_job(inst_id, bot, session_factory)   # не должен упасть наружу
