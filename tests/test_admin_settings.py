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

    await svc.execute_confirmed(confirm_cb.t, session)
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


# ---------------------------------------------------------------------------
# Регрессия (Task 24 review, Critical): AdminCb(k=f"{category}:{idx}") содержал
# буквальный ":" — служебный разделитель полей aiogram CallbackData — и
# .pack() бросал ValueError на КАЖДОЙ настройке в КАЖДОЙ категории. Все тесты
# выше были зелёными, потому что ни один из них реально не гонял .pack()/
# .unpack() для settings_list_keyboard/setting_card_keyboard (в отличие от
# ConfirmCb, где round-trip уже покрыт test_reset_goes_through_confirm_token...).
# Эти тесты обязаны реально вызывать .pack() (а не мокать), иначе баг снова
# останется невидимым для CI.
# ---------------------------------------------------------------------------

def test_settings_list_keyboard_pack_unpack_roundtrip_multi_digit_index():
    """article_check — категория с 19 настройками (двузначные индексы, включая
    18) — раньше .pack() падал с ValueError на каждой строке."""
    from bot.handlers.admin.settings import category_keys
    from bot.keyboards.admin.settings import settings_list_keyboard
    from bot.keyboards.admin.main import AdminCb

    keys = category_keys("article_check")
    assert len(keys) == 19
    entries = [(idx, key, f"{key} = 1") for idx, key in enumerate(keys)]
    kb = settings_list_keyboard("article_check", entries, page=1, total_pages=1)

    # первая строка на каждую настройку + пагинация (пропущена, total_pages=1) + "Назад"
    setting_rows = kb.inline_keyboard[:-1]
    assert len(setting_rows) == 19
    for idx, row in enumerate(setting_rows):
        packed = row[0].callback_data                  # .pack() уже вызван конструктором клавиатуры
        cb = AdminCb.unpack(packed)                     # реальный round-trip, не мок
        assert cb.s == "set" and cb.a == "card"
        assert cb.k == "article_check"
        assert cb.id == idx


def test_settings_list_keyboard_pagination_and_back_roundtrip():
    from bot.keyboards.admin.settings import settings_list_keyboard
    from bot.keyboards.admin.main import AdminCb

    entries = [(0, "approval.timeout_hours", "approval.timeout_hours = 24")]
    kb = settings_list_keyboard("approval", entries, page=2, total_pages=3)
    pag_row = kb.inline_keyboard[-2]
    prev_cb = AdminCb.unpack(pag_row[0].callback_data)
    next_cb = AdminCb.unpack(pag_row[2].callback_data)
    assert prev_cb.k == "approval" and prev_cb.p == 1
    assert next_cb.k == "approval" and next_cb.p == 3
    back_cb = AdminCb.unpack(kb.inline_keyboard[-1][0].callback_data)
    assert back_cb.a == "open"


def test_setting_card_keyboard_pack_unpack_roundtrip():
    """setting_card_keyboard — тот же баг, что и в settings_list_keyboard
    (реализатор занёс его повторно после того, как уже нашёл и починил
    аналогичный случай в ConfirmCb)."""
    from bot.keyboards.admin.settings import setting_card_keyboard
    from bot.keyboards.admin.main import AdminCb

    kb = setting_card_keyboard("article_check", 18)      # многозначный индекс
    edit_cb = AdminCb.unpack(kb.inline_keyboard[0][0].callback_data)
    reset_cb = AdminCb.unpack(kb.inline_keyboard[1][0].callback_data)
    back_cb = AdminCb.unpack(kb.inline_keyboard[2][0].callback_data)

    assert edit_cb.a == "edit" and edit_cb.k == "article_check" and edit_cb.id == 18
    assert reset_cb.a == "reset" and reset_cb.k == "article_check" and reset_cb.id == 18
    assert back_cb.a == "cat" and back_cb.k == "article_check" and back_cb.p == 1


async def test_show_settings_list_and_show_card_use_real_pack_unpack(session):
    """Сквозной happy-path: _show_settings_list рендерит клавиатуру реальными
    .pack()'ами, а _show_card восстанавливает category/idx из AdminCb.k/.id
    (не из парсинга строки) — воспроизводит ровно тот путь, что раньше падал
    в реальном Telegram (settings_list_keyboard('approval', [(0, ...)], 1, 1))."""
    from bot.handlers.admin.settings import _show_card, _show_settings_list
    from bot.keyboards.admin.main import AdminCb

    callback = AsyncMock()
    await _show_settings_list(callback, session, "approval", 1)
    reply_markup = callback.message.edit_text.await_args.kwargs.get("reply_markup") \
        or callback.message.edit_text.await_args.args[1]
    card_cb = AdminCb.unpack(reply_markup.inline_keyboard[0][0].callback_data)
    assert card_cb.a == "card" and card_cb.k == "approval"

    callback2 = AsyncMock()
    await _show_card(callback2, session, card_cb.k, card_cb.id)
    callback2.message.edit_text.assert_awaited()


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


async def test_delivery_log_settings_rebuild_delivery_log_job(session):
    """Часть Б: правка delivery_log.* через общий редактор настроек
    пересобирает ИМЕННО delivery_log-job, а не sync-job."""
    cfg, valya = await make_config(session)
    from bot.handlers.admin.settings import SCHEDULER_AFFECTING, apply_setting_input

    assert "delivery_log.enabled" in SCHEDULER_AFFECTING
    assert "delivery_log.interval_minutes" in SCHEDULER_AFFECTING

    scheduler_svc = AsyncMock()
    ok, _ = await apply_setting_input(session, valya, "delivery_log.interval_minutes",
                                      "30", scheduler_svc)
    assert ok is True
    scheduler_svc.register_delivery_log_job.assert_awaited()
    scheduler_svc.register_sync_job.assert_not_awaited()


async def test_status_notifications_settings_rebuild_status_notification_job(session):
    """Часть Г: правка status_notifications.enabled/.interval_minutes через
    общий редактор настроек пересобирает ИМЕННО status_notifications-job;
    statuses — НЕ scheduler-affecting (влияет только на фильтр job'а)."""
    cfg, valya = await make_config(session)
    from bot.handlers.admin.settings import SCHEDULER_AFFECTING, apply_setting_input

    assert "status_notifications.enabled" in SCHEDULER_AFFECTING
    assert "status_notifications.interval_minutes" in SCHEDULER_AFFECTING
    assert "status_notifications.statuses" not in SCHEDULER_AFFECTING

    scheduler_svc = AsyncMock()
    ok, _ = await apply_setting_input(session, valya,
                                      "status_notifications.interval_minutes",
                                      "10", scheduler_svc)
    assert ok is True
    scheduler_svc.register_status_notification_job.assert_awaited()
    scheduler_svc.register_sync_job.assert_not_awaited()
    scheduler_svc.register_delivery_log_job.assert_not_awaited()

    # statuses сохраняется без пересборки каких-либо job'ов
    scheduler_svc.reset_mock()
    ok, _ = await apply_setting_input(session, valya, "status_notifications.statuses",
                                      '["completed"]', scheduler_svc)
    assert ok is True
    scheduler_svc.register_status_notification_job.assert_not_awaited()


async def test_status_history_settings_rebuild_status_history_job(session):
    """Часть Д: правка status_history_log.* пересобирает ИМЕННО
    status_history-job, а не соседние job'ы."""
    cfg, valya = await make_config(session)
    from bot.handlers.admin.settings import SCHEDULER_AFFECTING, apply_setting_input

    assert "status_history_log.enabled" in SCHEDULER_AFFECTING
    assert "status_history_log.interval_minutes" in SCHEDULER_AFFECTING

    scheduler_svc = AsyncMock()
    ok, _ = await apply_setting_input(session, valya,
                                      "status_history_log.interval_minutes",
                                      "30", scheduler_svc)
    assert ok is True
    scheduler_svc.register_status_history_job.assert_awaited()
    scheduler_svc.register_sync_job.assert_not_awaited()
    scheduler_svc.register_delivery_log_job.assert_not_awaited()
    scheduler_svc.register_status_notification_job.assert_not_awaited()
