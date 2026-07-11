from datetime import datetime
from unittest.mock import AsyncMock

from bot.database.models import Role, TaskStatus
from bot.database.repositories.task_repository import TaskRepository
from bot.database.repositories.user_repository import UserRepository
from bot.services.setting_service import SettingService
from bot.services.task_service import TaskService
from tests.test_task_service import make_config


async def test_batch_size_applies_only_to_new_sessions(session):   # тесты 10, 11
    cfg, valya = await make_config(session)
    tsvc = TaskService(session)
    old_inst = await tsvc.create_instance_for(cfg, datetime(2026, 7, 10, 9))
    await SettingService(session).set("article_check.batch_size", 25, valya.id)
    new_inst = await tsvc.create_instance_for(cfg, datetime(2026, 7, 12, 9))
    await session.commit()
    assert old_inst.article_batch_size_snapshot == 15    # старая сессия — старый размер
    assert new_inst.article_batch_size_snapshot == 25    # новая — новый


async def test_auto_approve_change_affects_only_new_tasks(session):  # тесты 12, 13
    cfg, valya = await make_config(session)
    tsvc = TaskService(session)
    old_inst = await tsvc.create_instance_for(cfg, datetime(2026, 7, 10, 9))
    await SettingService(session).set("approval.timeout_hours", 6, valya.id)
    new_inst = await tsvc.create_instance_for(cfg, datetime(2026, 7, 12, 9))
    assert old_inst.approval_timeout_hours_snapshot == 24    # 24-часовой snapshot
    assert new_inst.approval_timeout_hours_snapshot == 6


async def test_settings_card_shows_current_value(session):
    """Карточка отображает текущее значение перед изменением."""
    cfg, valya = await make_config(session)
    from bot.handlers.admin.settings import render_setting_card
    text = await render_setting_card(session, "approval.timeout_hours")
    assert "24" in text and "int" in text


async def test_edit_value_validated_and_scheduler_rebuilt(session):
    cfg, valya = await make_config(session)
    from bot.handlers.admin.settings import apply_setting_input
    scheduler_svc = AsyncMock()
    # неверный тип отклоняется с русским сообщением
    ok, msg = await apply_setting_input(session, valya, "approval.timeout_hours",
                                        "abc", scheduler_svc)
    assert ok is False and "число" in msg
    # верное значение применяется
    ok, _ = await apply_setting_input(session, valya, "approval.timeout_hours",
                                      "48", scheduler_svc)
    assert ok is True
    assert await SettingService(session).get("approval.timeout_hours") == 48
    # настройка отчётов пересобирает report-job
    ok, _ = await apply_setting_input(session, valya, "reports.time", "21:30",
                                      scheduler_svc)
    assert ok is True
    scheduler_svc.register_report_job.assert_awaited()


# ---------------------------------------------------------------------------
# Whitelist / dangerous-operation coverage (обязательные проверки Tasks 17-23,
# явно затребованные в брифе Task 24: индекс -> ключ только через registry,
# сброс к default только через confirm_token с required_permission).
# ---------------------------------------------------------------------------

async def _owner(session):
    return await UserRepository(session).upsert(telegram_id=1, name="Owner", role=Role.OWNER)


def test_category_keys_sorted_and_editable_only():
    from bot.handlers.admin.settings import category_keys
    keys = category_keys("approval")
    assert keys == sorted(keys)
    assert "internal.settings_version" not in keys        # is_editable=False нигде не всплывает


def test_key_by_index_rejects_out_of_range_index():
    """Whitelist: индекс вне диапазона отклоняется, а не тихо маппится на что-то."""
    from bot.handlers.admin.settings import category_keys, key_by_index
    keys = category_keys("approval")
    try:
        key_by_index("approval", len(keys))                # первый невалидный индекс
        assert False, "ожидался KeyError"
    except KeyError as exc:
        assert "Неизвестная настройка" in str(exc)


def test_key_by_index_matches_category_keys():
    from bot.handlers.admin.settings import category_keys, key_by_index
    keys = category_keys("reminders")
    for idx, key in enumerate(keys):
        assert key_by_index("reminders", idx) == key


async def test_reset_goes_through_confirm_token_with_settings_manage(session):
    """Сброс к default — опасная операция: должен идти через
    AdminService.confirm_token(required_permission="settings.manage", ...), а
    не выполняться напрямую из callback."""
    owner = await _owner(session)
    await session.commit()
    await SettingService(session).set("approval.timeout_hours", 99, owner.id)
    await session.commit()

    from bot.handlers.admin.settings import _start_reset, category_keys
    from bot.keyboards.admin.confirm import ConfirmCb
    from bot.services.admin_service import AdminService

    svc = AdminService(session)
    callback = AsyncMock()
    idx = category_keys("approval").index("approval.timeout_hours")
    await _start_reset(callback, session, owner, svc, "approval", idx)

    # значение НЕ изменилось сразу — операция лишь поставлена в очередь подтверждения
    assert await SettingService(session).get("approval.timeout_hours") == 99

    reply_markup = callback.message.edit_text.await_args.kwargs.get("reply_markup") \
        or callback.message.edit_text.await_args.args[1]
    token = reply_markup.inline_keyboard[0][0].callback_data
    confirm_cb = ConfirmCb.unpack(token)

    entry = svc.get_pending(confirm_cb.t)
    assert entry is not None
    assert entry.required_permission == "settings.manage"
    assert entry.creator_actor_id == owner.id

    await svc.execute_confirmed(confirm_cb.t)
    assert await SettingService(session).get("approval.timeout_hours") == 24  # default


async def test_reset_rejects_out_of_range_index_without_creating_token(session):
    owner = await _owner(session)
    await session.commit()
    from bot.handlers.admin.settings import _start_reset, category_keys
    from bot.services.admin_service import AdminService

    svc = AdminService(session)
    callback = AsyncMock()
    bad_idx = len(category_keys("approval"))
    await _start_reset(callback, session, owner, svc, "approval", bad_idx)

    callback.answer.assert_awaited()
    assert "Неизвестная настройка" in callback.answer.await_args.args[0]
    callback.message.edit_text.assert_not_awaited()          # никакого токена не создано


async def test_settings_message_handler_denies_actor_without_permission(session):
    """FSM-продолжение (обычное сообщение) не проходит через resolve_admin —
    handler обязан сам проверить actor и право settings.manage."""
    manager = await UserRepository(session).upsert(
        telegram_id=5, name="M", role=Role.MANAGER_WB)
    await session.commit()

    from bot.handlers.admin.settings import handle_value_message
    from bot.states.admin_states import AdminStates

    state = AsyncMock()
    state.get_data = AsyncMock(return_value={"setting_key": "approval.timeout_hours"})
    message = AsyncMock()
    message.from_user.id = manager.telegram_id
    message.text = "48"

    await handle_value_message(message, session, state)

    assert await SettingService(session).get("approval.timeout_hours") == 24  # не изменилось
    message.answer.assert_awaited_once()
    assert "прав" in message.answer.await_args.args[0].lower()
    state.clear.assert_awaited()


async def test_settings_message_handler_applies_value_for_authorized_actor(session):
    owner = await _owner(session)
    await session.commit()

    from bot.handlers.admin.settings import handle_value_message

    state = AsyncMock()
    state.get_data = AsyncMock(return_value={"setting_key": "approval.timeout_hours"})
    message = AsyncMock()
    message.from_user.id = owner.telegram_id
    message.text = "48"

    await handle_value_message(message, session, state)

    assert await SettingService(session).get("approval.timeout_hours") == 48
    state.clear.assert_awaited()


async def test_rem_section_opens_reminders_and_approval_categories(session):
    owner = await _owner(session)
    await session.commit()
    from bot.handlers.admin.settings import handle_reminders_section
    from bot.keyboards.admin.main import AdminCb
    from bot.services.admin_service import AdminService

    svc = AdminService(session)
    callback = AsyncMock()
    await handle_reminders_section(callback, AdminCb(s="rem", a="open"), session, owner, svc)

    reply_markup = callback.message.edit_text.await_args.kwargs.get("reply_markup") \
        or callback.message.edit_text.await_args.args[1]
    payloads = [row[0].callback_data for row in reply_markup.inline_keyboard[:-1]]
    categories = [AdminCb.unpack(p).k for p in payloads]
    assert categories == ["reminders", "approval"]
