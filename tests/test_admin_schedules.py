from unittest.mock import AsyncMock

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from bot.database.models import Role
from bot.database.repositories.task_repository import TaskRepository
from bot.database.repositories.user_repository import UserRepository
from bot.services.scheduler_service import SchedulerService
from tests.test_task_service import make_config


async def _owner(session):
    return await UserRepository(session).upsert(telegram_id=1, name="O", role=Role.OWNER)


class FakeState:
    def __init__(self):
        self.data: dict = {}
        self.state = None

    async def get_data(self):
        return dict(self.data)

    async def update_data(self, **kwargs):
        self.data.update(kwargs)

    async def set_state(self, state):
        self.state = state

    async def clear(self):
        self.data = {}
        self.state = None


# ---------------------------------------------------------------------------
# Step 1 (брифовые тесты, дословно по сигнатуре/сценарию; adapted к тому, что
# apply_schedule_field теперь коммитит ВНУТРИ себя перед rebuild — см. docstring
# bot/handlers/admin/schedules.py, п.2 отклонений).
# ---------------------------------------------------------------------------

async def test_apply_schedule_field_rebuilds_without_duplicate(session_factory):
    from bot.handlers.admin.schedules import apply_schedule_field

    async with session_factory() as s:
        user = await UserRepository(s).upsert(telegram_id=1, name="V", role=Role.MANAGER_WB)
        owner = await UserRepository(s).upsert(telegram_id=2, name="O", role=Role.OWNER)
        cfg = await TaskRepository(s).upsert_config(dict(
            external_task_id="t", title="t", schedule_type="every_n_days",
            schedule_interval=2, responsible_user_id=user.id, is_active=True))
        await s.commit()
        cfg_id, owner_id = cfg.id, owner.id

    scheduler = AsyncIOScheduler()
    svc = SchedulerService(scheduler, AsyncMock(), session_factory)
    async with session_factory() as s:
        owner = await UserRepository(s).get_by_id(owner_id)
        ok, _ = await apply_schedule_field(s, owner, cfg_id, "schedule_interval", "3", svc)
        await s.commit()                       # no-op: apply_schedule_field уже закоммитила
        assert ok is True
        ok, _ = await apply_schedule_field(s, owner, cfg_id, "schedule_interval", "3", svc)
        await s.commit()
    jobs = [j for j in scheduler.get_jobs() if j.id == f"config:{cfg_id}"]
    assert len(jobs) == 1


async def test_invalid_cron_rejected(session):
    owner = await UserRepository(session).upsert(telegram_id=2, name="O", role=Role.OWNER)
    cfg = await TaskRepository(session).upsert_config(dict(
        external_task_id="t2", title="t", schedule_type="cron", is_active=True))
    await session.commit()
    from bot.handlers.admin.schedules import apply_schedule_field
    ok, msg = await apply_schedule_field(session, owner, cfg.id, "schedule_value",
                                         "not a cron", None)
    assert ok is False and "cron" in msg.lower()


# ---------------------------------------------------------------------------
# Фиксы сверх брифа: schedule_type против ScheduleType, weekly/monthly
# диапазоны, first_run_date как date (не строка).
# ---------------------------------------------------------------------------

async def test_schedule_type_rejects_unknown_value(session):
    owner = await _owner(session)
    cfg, _ = await make_config(session)
    from bot.handlers.admin.schedules import apply_schedule_field
    ok, msg = await apply_schedule_field(session, owner, cfg.id, "schedule_type", "не расписание", None)
    assert ok is False and "тип" in msg.lower()
    assert cfg.schedule_type != "не расписание"


async def test_schedule_value_weekly_out_of_range_rejected(session):
    owner = await _owner(session)
    cfg, _ = await make_config(session)
    cfg.schedule_type = "weekly"
    await session.commit()
    from bot.handlers.admin.schedules import apply_schedule_field
    ok, msg = await apply_schedule_field(session, owner, cfg.id, "schedule_value", "9", None)
    assert ok is False


async def test_schedule_value_monthly_valid_stored_as_string(session):
    owner = await _owner(session)
    cfg, _ = await make_config(session)
    cfg.schedule_type = "monthly"
    await session.commit()
    from bot.handlers.admin.schedules import apply_schedule_field
    ok, _ = await apply_schedule_field(session, owner, cfg.id, "schedule_value", "15", None)
    assert ok is True and cfg.schedule_value == "15"


async def test_first_run_date_parsed_as_date_object(session):
    from datetime import date

    owner = await _owner(session)
    cfg, _ = await make_config(session)
    from bot.handlers.admin.schedules import apply_schedule_field
    ok, _ = await apply_schedule_field(session, owner, cfg.id, "first_run_date", "2026-08-01", None)
    assert ok is True
    assert cfg.first_run_date == date(2026, 8, 1)


async def test_bool_field_toggle_via_raw_flag(session):
    owner = await _owner(session)
    cfg, _ = await make_config(session)
    cfg.run_on_weekends = False
    await session.commit()
    from bot.handlers.admin.schedules import apply_schedule_field
    ok, _ = await apply_schedule_field(session, owner, cfg.id, "run_on_weekends", "1", None)
    assert ok is True and cfg.run_on_weekends is True


async def test_field_outside_whitelist_rejected(session):
    owner = await _owner(session)
    cfg, _ = await make_config(session)
    from bot.handlers.admin.schedules import apply_schedule_field
    ok, msg = await apply_schedule_field(session, owner, cfg.id, "title", "hack", None)
    assert ok is False and "недоступно" in msg.lower()


# ---------------------------------------------------------------------------
# actor-check
# ---------------------------------------------------------------------------

async def test_sch_callback_rejects_actor_without_permission(session):
    from bot.handlers.admin.schedules import handle_sch_callback
    from bot.keyboards.admin.schedules import SchCb

    logistic = await UserRepository(session).upsert(telegram_id=5, name="L", role=Role.LOGISTIC)
    await session.commit()

    callback = AsyncMock()
    callback.from_user.id = logistic.telegram_id
    await handle_sch_callback(callback, SchCb(a="list"), session)
    callback.answer.assert_awaited_with("Недостаточно прав", show_alert=True)


async def test_sch_callback_rejects_unregistered_user(session):
    from bot.handlers.admin.schedules import handle_sch_callback
    from bot.keyboards.admin.schedules import SchCb

    callback = AsyncMock()
    callback.from_user.id = 999999
    await handle_sch_callback(callback, SchCb(a="list"), session)
    callback.answer.assert_awaited_with("Недостаточно прав", show_alert=True)


# ---------------------------------------------------------------------------
# Список / карточка
# ---------------------------------------------------------------------------

async def test_show_schedules_list_only_active_configs(session):
    from bot.handlers.admin.schedules import handle_sch_callback
    from bot.keyboards.admin.schedules import SchCb

    owner = await _owner(session)
    cfg, _ = await make_config(session)
    inactive = await TaskRepository(session).upsert_config(dict(
        external_task_id="inactive", title="Неактивный", schedule_type="daily", is_active=False))
    await session.commit()

    callback = AsyncMock()
    callback.from_user.id = owner.telegram_id
    await handle_sch_callback(callback, SchCb(a="list"), session)
    kb = callback.message.edit_text.await_args.kwargs.get("reply_markup") \
        or callback.message.edit_text.await_args.args[1]
    ids = [SchCb.unpack(row[0].callback_data).id for row in kb.inline_keyboard
          if row and row[0].callback_data.startswith("sc:card")]
    assert cfg.id in ids
    assert inactive.id not in ids


async def test_show_card_renders_next_run_at(session):
    from bot.handlers.admin.schedules import handle_sch_callback
    from bot.keyboards.admin.schedules import SchCb

    owner = await _owner(session)
    cfg, _ = await make_config(session)

    callback = AsyncMock()
    callback.from_user.id = owner.telegram_id
    await handle_sch_callback(callback, SchCb(a="card", id=cfg.id), session)
    text = callback.message.edit_text.await_args.args[0]
    assert "every_n_days" in text
    assert "Ближайший запуск" in text


async def test_cancel_button_clears_fsm_state_no_stray_text_applied(session):
    """Регресс на находку ревью Task 28: кнопка «❌ Отменить» (SchCb(a="card"))
    ОБЯЗАНА сбросить waiting_sch_edit — до фикса состояние оставалось активным,
    и следующее произвольное текстовое сообщение пользователя (он думает, что
    отменил) молча перехватывалось handle_sch_edit_message и применялось как
    новое значение поля (воспроизведено ревью: клик "Отменить" -> текст "42"
    -> schedule_interval молча стал 42)."""
    from bot.handlers.admin.schedules import handle_sch_callback
    from bot.keyboards.admin.schedules import SchCb
    from bot.states.admin_states import AdminStates

    owner = await _owner(session)
    cfg, _ = await make_config(session)
    cfg.schedule_interval = 2
    await session.commit()

    state = FakeState()
    callback = AsyncMock()
    callback.from_user.id = owner.telegram_id
    # Начать редактирование schedule_interval (индекс 2 в SCHEDULE_FIELD_LIST).
    await handle_sch_callback(callback, SchCb(a="editfield", id=cfg.id, k="2"), session, state)
    assert state.state == AdminStates.waiting_sch_edit
    assert state.data.get("field") == "schedule_interval"

    # Пользователь жмёт «❌ Отменить» -> SchCb(a="card", id=cfg.id).
    await handle_sch_callback(callback, SchCb(a="card", id=cfg.id), session, state)
    # aiogram маршрутизирует message-хендлеры по текущему FSM-состоянию —
    # раз state сброшен, handle_sch_edit_message для этого чата больше не
    # вызовется вообще; здесь мы проверяем именно причину бага напрямую.
    assert state.state is None and state.data == {}
    assert cfg.schedule_interval == 2                   # значение не тронуто


async def test_show_card_unknown_config_rejected(session):
    from bot.handlers.admin.schedules import handle_sch_callback
    from bot.keyboards.admin.schedules import SchCb

    owner = await _owner(session)
    callback = AsyncMock()
    callback.from_user.id = owner.telegram_id
    await handle_sch_callback(callback, SchCb(a="card", id=999999), session)
    callback.answer.assert_awaited_with("Шаблон не найден", show_alert=True)


# ---------------------------------------------------------------------------
# Regression (Tasks 24-27 lesson): каждая новая клавиатура — реальный
# .pack()/.unpack() round-trip. Ни одно поле SchCb не содержит ":", default
# SchCb.k — str | None = None, НЕ str = "".
# ---------------------------------------------------------------------------

def test_schcb_default_fields_roundtrip_without_explicit_values():
    from bot.keyboards.admin.schedules import SchCb

    cb = SchCb(a="noop")
    packed = cb.pack()
    unpacked = SchCb.unpack(packed)
    assert unpacked.a == "noop"
    assert unpacked.id == 0
    assert unpacked.p == 1
    assert unpacked.k is None


def test_schedules_list_keyboard_pack_unpack_roundtrip_pagination_and_back():
    from bot.keyboards.admin.main import AdminCb
    from bot.keyboards.admin.schedules import SchCb, schedules_list_keyboard

    entries = [(i, f"Шаблон {i}") for i in range(1, 4)]
    kb = schedules_list_keyboard(entries, page=2, total_pages=3)
    for row in kb.inline_keyboard[:3]:
        cb = SchCb.unpack(row[0].callback_data)
        assert cb.a == "card"
    nav = kb.inline_keyboard[3]
    assert SchCb.unpack(nav[0].callback_data).p == 1
    assert SchCb.unpack(nav[2].callback_data).p == 3
    back = AdminCb.unpack(kb.inline_keyboard[-1][0].callback_data)
    assert back.s == "sch" and back.a == "menu"


def test_schedule_card_keyboard_pack_unpack_roundtrip():
    from bot.keyboards.admin.schedules import SchCb, schedule_card_keyboard

    kb = schedule_card_keyboard(config_id=987654321)
    edit = SchCb.unpack(kb.inline_keyboard[0][0].callback_data)
    assert edit.a == "fields" and edit.id == 987654321 and edit.p == 1
    back = SchCb.unpack(kb.inline_keyboard[1][0].callback_data)
    assert back.a == "list"


def test_schedule_fields_keyboard_pack_unpack_roundtrip_multi_digit_index():
    from bot.keyboards.admin.schedules import SchCb, schedule_fields_keyboard

    entries = [(i, f"Поле {i}") for i in range(8)]     # весь SCHEDULE_FIELD_LIST
    kb = schedule_fields_keyboard(config_id=5, entries=entries, page=1, total_pages=1)
    for row, (idx, _label) in zip(kb.inline_keyboard[:-1], entries):
        cb = SchCb.unpack(row[0].callback_data)
        assert cb.a == "editfield" and cb.id == 5 and int(cb.k) == idx
    back = SchCb.unpack(kb.inline_keyboard[-1][0].callback_data)
    assert back.a == "card" and back.id == 5


def test_schedule_type_choice_keyboard_pack_unpack_roundtrip():
    from bot.handlers.admin.schedules import SCHEDULE_TYPE_VALUES
    from bot.keyboards.admin.schedules import SchCb, schedule_type_choice_keyboard

    values = sorted(SCHEDULE_TYPE_VALUES)
    kb = schedule_type_choice_keyboard(config_id=7, values=values)
    for row, value in zip(kb.inline_keyboard[:-1], values):
        cb = SchCb.unpack(row[0].callback_data)
        assert cb.a == "settype" and cb.id == 7 and cb.k == value


def test_weekday_choice_keyboard_pack_unpack_roundtrip():
    from bot.keyboards.admin.schedules import SchCb, weekday_choice_keyboard

    kb = weekday_choice_keyboard(config_id=3)
    for i, row in enumerate(kb.inline_keyboard[:-1]):
        cb = SchCb.unpack(row[0].callback_data)
        assert cb.a == "setweekday" and cb.id == 3 and cb.k == str(i)


def test_cancel_edit_keyboard_pack_unpack_roundtrip():
    from bot.keyboards.admin.schedules import SchCb, cancel_edit_keyboard

    kb = cancel_edit_keyboard(config_id=11)
    cb = SchCb.unpack(kb.inline_keyboard[0][0].callback_data)
    assert cb.a == "card" and cb.id == 11


def test_due_days_offset_registered_in_field_lists():
    from bot.handlers.admin.schedules import (
        FIELD_TITLES, SCHEDULE_FIELD_LIST, SCHEDULE_FIELDS,
    )
    assert "due_days_offset" in SCHEDULE_FIELDS
    # добавлено строго В КОНЕЦ: вставка в середину сдвинула бы индексы
    # whitelist-редактирования по idx в SCHEDULE_FIELD_LIST
    assert SCHEDULE_FIELD_LIST[-1] == "due_days_offset"
    assert FIELD_TITLES["due_days_offset"] == "Срок: дней после отправки"


async def test_due_days_offset_valid_value_applies(session):
    owner = await _owner(session)
    cfg, _ = await make_config(session)
    from bot.handlers.admin.schedules import apply_schedule_field
    ok, _ = await apply_schedule_field(session, owner, cfg.id, "due_days_offset", "2", None)
    assert ok is True and cfg.due_days_offset == 2


async def test_due_days_offset_rejects_out_of_range(session):
    owner = await _owner(session)
    cfg, _ = await make_config(session)
    from bot.handlers.admin.schedules import apply_schedule_field
    ok, _ = await apply_schedule_field(session, owner, cfg.id, "due_days_offset", "-1", None)
    assert ok is False
    ok, _ = await apply_schedule_field(session, owner, cfg.id, "due_days_offset", "31", None)
    assert ok is False
    assert cfg.due_days_offset == 0                       # значение не тронуто


async def test_schedule_card_shows_due_days_offset(session):
    cfg, _ = await make_config(session)
    cfg.due_days_offset = 3
    await session.commit()
    from bot.handlers.admin.schedules import render_schedule_card
    assert "Срок: дней после отправки: 3" in render_schedule_card(cfg)


# ---------------------------------------------------------------------------
# МСК<->UTC для поля time (баг: планировщик работает в naive-UTC, а admin
# вводит время по МСК — без конвертации задача уходила на 3 часа позже).
# due_time сознательно НЕ конвертируется (используется только текстом в
# сообщении сотруднику, как есть — см. bot/utils/datetime_utils.moscow_to_utc).
# ---------------------------------------------------------------------------

async def test_apply_schedule_field_time_converts_msk_to_utc(session):
    from datetime import time
    owner = await _owner(session)
    cfg, _ = await make_config(session)
    from bot.handlers.admin.schedules import apply_schedule_field
    ok, _ = await apply_schedule_field(session, owner, cfg.id, "time", "09:30", None)
    assert ok is True
    assert cfg.time == time(6, 30)


async def test_apply_schedule_field_due_time_not_converted(session):
    from datetime import time
    owner = await _owner(session)
    cfg, _ = await make_config(session)
    from bot.handlers.admin.schedules import apply_schedule_field
    ok, _ = await apply_schedule_field(session, owner, cfg.id, "due_time", "18:00", None)
    assert ok is True
    assert cfg.due_time == time(18, 0)


async def test_schedule_card_shows_time_in_msk(session):
    from datetime import time
    cfg, _ = await make_config(session)
    cfg.time = time(6, 30)                                 # хранится в UTC
    await session.commit()
    from bot.handlers.admin.schedules import render_schedule_card
    assert "Время (МСК): 09:30" in render_schedule_card(cfg)
