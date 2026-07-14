"""Тесты уведомлений owner/partner о смене статуса задач (Часть Г).

Poll-модель: job читает TaskLog (все переходы уже пишутся туда через
TaskRepository.transition_status), сами 8 вызовов transition_status не
трогаются. Список уведомляемых статусов — настройка
status_notifications.statuses, читается при каждом прогоне.
"""
from datetime import datetime
from unittest.mock import AsyncMock

from bot.database.models import Role, TaskLog
from bot.database.repositories.task_repository import TaskRepository
from bot.database.repositories.user_repository import UserRepository
from bot.services.setting_service import SettingService
from bot.services.status_notification_service import status_notification_job


async def seed_logs(session_factory) -> dict[str, int]:
    """Owner (tg=99), исполнитель Валя, инстанс и четыре записи TaskLog:
    notify — created -> in_progress от Вали (в default-списке),
    auto  — авто-переход в overdue (user_id IS NULL, в default-списке),
    done  — in_progress -> completed от Вали (в default-списке),
    other — переход в waiting_approval (ВНЕ default-списка)."""
    from bot.services.task_service import TaskService

    async with session_factory() as s:
        await UserRepository(s).upsert(telegram_id=99, name="Хозяин", role=Role.OWNER)
        valya = await UserRepository(s).upsert(telegram_id=10, name="Валя",
                                               role=Role.MANAGER_WB)
        cfg = await TaskRepository(s).upsert_config(dict(
            external_task_id="notify_me", title="Проверка", scenario="simple",
            schedule_type="daily", responsible_user_id=valya.id, is_active=True))
        inst = await TaskService(s).create_instance_for(cfg, datetime(2026, 7, 10, 9, 0))
        notify = TaskLog(task_instance_id=inst.id, user_id=valya.id, action="task.take",
                         old_status="created", new_status="in_progress")
        auto = TaskLog(task_instance_id=inst.id, user_id=None, action="auto:overdue",
                       old_status="in_progress", new_status="overdue")
        done = TaskLog(task_instance_id=inst.id, user_id=valya.id, action="task.done",
                       old_status="in_progress", new_status="completed")
        other = TaskLog(task_instance_id=inst.id, user_id=valya.id, action="task.approve",
                        old_status="in_progress", new_status="waiting_approval")
        s.add_all([notify, auto, done, other])
        await s.commit()
        return {"notify": notify.id, "auto": auto.id,
                "done": done.id, "other": other.id}


async def _enable(session_factory) -> None:
    async with session_factory() as s:
        await SettingService(s).set("status_notifications.enabled", True,
                                    actor_user_id=None)
        await s.commit()


async def _set_statuses(session_factory, statuses: list[str]) -> None:
    async with session_factory() as s:
        await SettingService(s).set("status_notifications.statuses", statuses,
                                    actor_user_id=None)
        await s.commit()


async def test_disabled_by_default_sends_nothing(session_factory):
    ids = await seed_logs(session_factory)
    bot = AsyncMock()
    await status_notification_job(bot, session_factory)   # enabled=False по умолчанию
    bot.send_message.assert_not_awaited()
    async with session_factory() as s:
        assert (await s.get(TaskLog, ids["notify"])).owner_notified_at is None


async def test_notifies_owner_once_with_default_statuses(session_factory):
    """Default-список (4 статуса): уведомляются notify/auto/done ровно один
    раз; other (waiting_approval) — нет. Текст содержит задачу, переход и кто
    сменил (имя пользователя или action для авто-перехода)."""
    ids = await seed_logs(session_factory)
    await _enable(session_factory)
    bot = AsyncMock()
    await status_notification_job(bot, session_factory)
    await status_notification_job(bot, session_factory)   # повторный прогон — без дублей

    assert bot.send_message.await_count == 3              # notify + auto + done
    calls = bot.send_message.await_args_list
    assert {c.kwargs["chat_id"] for c in calls} == {99}   # только owner
    texts = [c.kwargs["text"] for c in calls]
    assert any("Проверка" in t and "created → in_progress" in t and "Валя" in t
               for t in texts)
    assert any("in_progress → overdue" in t and "auto:overdue" in t for t in texts)
    assert any("in_progress → completed" in t for t in texts)
    async with session_factory() as s:
        for key in ("notify", "auto", "done"):
            assert (await s.get(TaskLog, ids[key])).owner_notified_at is not None
        assert (await s.get(TaskLog, ids["other"])).owner_notified_at is None


async def test_statuses_setting_is_dynamic_filter(session_factory):
    """status_notifications.statuses читается при КАЖДОМ прогоне: сужение до
    ["completed"] отключает in_progress/overdue, но completed уведомляет;
    расширение списком с waiting_approval включает и его."""
    ids = await seed_logs(session_factory)
    await _enable(session_factory)
    await _set_statuses(session_factory, ["completed"])
    bot = AsyncMock()
    await status_notification_job(bot, session_factory)

    assert bot.send_message.await_count == 1              # только done
    assert "in_progress → completed" in bot.send_message.await_args.kwargs["text"]
    async with session_factory() as s:
        assert (await s.get(TaskLog, ids["done"])).owner_notified_at is not None
        assert (await s.get(TaskLog, ids["notify"])).owner_notified_at is None
        assert (await s.get(TaskLog, ids["auto"])).owner_notified_at is None

    await _set_statuses(session_factory, ["completed", "waiting_approval"])
    await status_notification_job(bot, session_factory)

    assert bot.send_message.await_count == 2              # +1: только other
    assert "waiting_approval" in bot.send_message.await_args.kwargs["text"]
    async with session_factory() as s:
        assert (await s.get(TaskLog, ids["other"])).owner_notified_at is not None
        assert (await s.get(TaskLog, ids["notify"])).owner_notified_at is None


async def test_targets_add_partner_recipients(session_factory):
    await seed_logs(session_factory)
    await _enable(session_factory)
    async with session_factory() as s:
        await UserRepository(s).upsert(telegram_id=77, name="Партнёр", role=Role.PARTNER)
        await SettingService(s).set("status_notifications.targets",
                                    ["owner", "partner"], actor_user_id=None)
        await s.commit()
    bot = AsyncMock()
    await status_notification_job(bot, session_factory)
    chats = sorted(c.kwargs["chat_id"] for c in bot.send_message.await_args_list)
    assert chats == [77, 77, 77, 99, 99, 99]              # 3 записи x 2 получателя


async def test_send_failure_does_not_break_job(session_factory):
    """Заблокировавший бота получатель не рушит job и не зацикливает рассылку:
    запись всё равно помечается обработанной (тот же паттерн, что эскалация
    в overdue_job)."""
    ids = await seed_logs(session_factory)
    await _enable(session_factory)
    bot = AsyncMock()
    bot.send_message.side_effect = RuntimeError("bot was blocked by the user")
    await status_notification_job(bot, session_factory)   # не должен упасть наружу
    async with session_factory() as s:
        assert (await s.get(TaskLog, ids["notify"])).owner_notified_at is not None
