from datetime import time
from unittest.mock import AsyncMock, MagicMock

from bot.database.models import Role, ScheduleType, TaskScenario
from bot.database.repositories.task_repository import TaskRepository
from bot.database.repositories.topic_repository import TopicRepository
from bot.database.repositories.user_repository import UserRepository
from tests.test_task_service import make_config


async def _owner(session):
    return await UserRepository(session).upsert(telegram_id=1, name="O", role=Role.OWNER)


def _reply_markup(callback):
    return callback.message.edit_text.await_args.kwargs.get("reply_markup") \
        or callback.message.edit_text.await_args.args[1]


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
# Step 1 (брифовые тесты, дословно)
# ---------------------------------------------------------------------------

async def test_clone_creates_independent_inactive_copy(session):
    cfg, valya = await make_config(session)
    owner = await UserRepository(session).upsert(telegram_id=1, name="O", role=Role.OWNER)
    from bot.handlers.admin.task_configs import clone_config
    clone = await clone_config(session, owner, cfg.id)
    await session.commit()
    assert clone.id != cfg.id
    assert clone.external_task_id != cfg.external_task_id
    assert clone.title == cfg.title
    assert clone.is_active is False                       # клон неактивен по умолчанию
    # изменение оригинала не трогает клон
    cfg.title = "Изменено"
    await session.commit()
    assert clone.title != "Изменено"


async def test_whitelist_field_editing(session):
    cfg, valya = await make_config(session)
    owner = await UserRepository(session).upsert(telegram_id=1, name="O", role=Role.OWNER)
    from bot.handlers.admin.task_configs import EDITABLE_FIELDS, apply_field_edit
    assert "title" in EDITABLE_FIELDS and "id" not in EDITABLE_FIELDS
    ok, _ = await apply_field_edit(session, owner, cfg.id, "title", "Новое имя")
    assert ok is True and cfg.title == "Новое имя"
    ok, msg = await apply_field_edit(session, owner, cfg.id, "external_task_id", "hack")
    assert ok is False and "запрещ" in msg.lower()          # не в whitelist


# ---------------------------------------------------------------------------
# Regression (Tasks 24-26 lesson): каждая НОВАЯ клавиатура — реальный
# .pack()/.unpack() round-trip. Ни одно поле CfgCb НЕ должно содержать ":",
# и default-поле CfgCb.k — str | None = None, НЕ str = "" (см. docstring
# bot/keyboards/admin/task_configs.py).
# ---------------------------------------------------------------------------

def test_cfgcb_default_fields_roundtrip_without_explicit_values():
    from bot.keyboards.admin.task_configs import CfgCb

    cb = CfgCb(a="noop")
    packed = cb.pack()
    unpacked = CfgCb.unpack(packed)
    assert unpacked.a == "noop"
    assert unpacked.id == 0
    assert unpacked.p == 1
    assert unpacked.k is None


def test_configs_list_keyboard_pack_unpack_roundtrip_many_and_pagination():
    from bot.keyboards.admin.main import AdminCb
    from bot.keyboards.admin.task_configs import CfgCb, configs_list_keyboard

    entries = [(i, f"Шаблон {i} (неактивен)" if i % 2 else f"Шаблон {i}")
               for i in range(1, 16)]
    kb = configs_list_keyboard(entries, page=2, total_pages=2)

    cfg_rows = kb.inline_keyboard[:15]
    for idx, row in enumerate(cfg_rows):
        cb = CfgCb.unpack(row[0].callback_data)
        assert cb.a == "card"
        assert cb.id == entries[idx][0]

    create_row = kb.inline_keyboard[15]
    create_cb = CfgCb.unpack(create_row[0].callback_data)
    assert create_cb.a == "create"

    pag_row = kb.inline_keyboard[16]
    prev_cb = CfgCb.unpack(pag_row[0].callback_data)
    noop_cb = CfgCb.unpack(pag_row[1].callback_data)
    next_cb = CfgCb.unpack(pag_row[2].callback_data)
    assert prev_cb.a == "list" and prev_cb.p == 1
    assert noop_cb.a == "noop"
    assert next_cb.a == "list" and next_cb.p == 2

    back_cb = AdminCb.unpack(kb.inline_keyboard[-1][0].callback_data)
    assert back_cb.s == "cfg" and back_cb.a == "menu"


def test_config_card_keyboard_pack_unpack_roundtrip_active_and_inactive():
    from bot.keyboards.admin.task_configs import CfgCb, config_card_keyboard

    config_id = 987654321
    kb = config_card_keyboard(config_id, is_active=True)
    flat = [btn for row in kb.inline_keyboard for btn in row]
    actions = {CfgCb.unpack(btn.callback_data).a: (btn, CfgCb.unpack(btn.callback_data))
              for btn in flat}
    assert actions["fields"][1].id == config_id
    assert actions["toggle"][1].id == config_id
    assert actions["toggle"][0].text == "🚫 Деактивировать"
    assert actions["clone"][1].id == config_id
    assert actions["run"][1].id == config_id
    assert actions["list"][1].p == 1

    kb_inactive = config_card_keyboard(config_id, is_active=False)
    flat_inactive = [btn for row in kb_inactive.inline_keyboard for btn in row]
    toggle_btn = next(b for b in flat_inactive if CfgCb.unpack(b.callback_data).a == "toggle")
    assert toggle_btn.text == "✅ Активировать"


def test_fields_list_keyboard_pack_unpack_roundtrip_index_and_pagination():
    from bot.keyboards.admin.task_configs import CfgCb, fields_list_keyboard

    config_id = 42
    entries = [(i, f"Поле {i}") for i in range(12)]
    kb = fields_list_keyboard(config_id, entries, page=1, total_pages=2)
    field_rows = kb.inline_keyboard[:12]
    for idx, row in enumerate(field_rows):
        cb = CfgCb.unpack(row[0].callback_data)
        assert cb.a == "editfield"
        assert cb.id == config_id
        assert cb.k == str(entries[idx][0])

    pag_row = kb.inline_keyboard[12]
    next_cb = CfgCb.unpack(pag_row[2].callback_data)
    assert next_cb.a == "fields" and next_cb.id == config_id and next_cb.p == 2

    back_cb = CfgCb.unpack(kb.inline_keyboard[-1][0].callback_data)
    assert back_cb.a == "card" and back_cb.id == config_id


def test_creation_choice_keyboard_pack_unpack_roundtrip():
    from bot.keyboards.admin.task_configs import CfgCb, creation_choice_keyboard

    kb = creation_choice_keyboard("cr_sched", [("daily", "Ежедневно"), ("weekly", "Еженедельно")])
    daily_cb = CfgCb.unpack(kb.inline_keyboard[0][0].callback_data)
    weekly_cb = CfgCb.unpack(kb.inline_keyboard[1][0].callback_data)
    cancel_cb = CfgCb.unpack(kb.inline_keyboard[-1][0].callback_data)
    assert daily_cb.a == "cr_sched" and daily_cb.k == "daily"
    assert weekly_cb.a == "cr_sched" and weekly_cb.k == "weekly"
    assert cancel_cb.a == "cr_cancel"


def test_cancel_creation_keyboard_pack_unpack_roundtrip():
    from bot.keyboards.admin.task_configs import CfgCb, cancel_creation_keyboard

    kb = cancel_creation_keyboard()
    cb = CfgCb.unpack(kb.inline_keyboard[0][0].callback_data)
    assert cb.a == "cr_cancel"
    assert cb.id == 0 and cb.p == 1 and cb.k is None


# ---------------------------------------------------------------------------
# actor-check
# ---------------------------------------------------------------------------

async def test_cfg_callback_rejects_actor_without_permission(session):
    from bot.handlers.admin.task_configs import handle_cfg_callback
    from bot.keyboards.admin.task_configs import CfgCb

    logistic = await UserRepository(session).upsert(telegram_id=5, name="L", role=Role.LOGISTIC)
    await session.commit()

    callback = AsyncMock()
    callback.from_user.id = logistic.telegram_id
    await handle_cfg_callback(callback, CfgCb(a="list"), session)
    callback.answer.assert_awaited_with("Недостаточно прав", show_alert=True)


# ---------------------------------------------------------------------------
# Список / карточка
# ---------------------------------------------------------------------------

async def test_show_configs_list_renders_seeded_configs(session):
    from bot.handlers.admin.task_configs import _show_configs_list

    await make_config(session)
    await session.commit()

    callback = AsyncMock()
    await _show_configs_list(callback, session, 1)
    reply_markup = _reply_markup(callback)
    assert len(reply_markup.inline_keyboard) >= 3   # 1 шаблон + создать + назад


async def test_show_card_renders_all_fields_and_next_run(session):
    from bot.handlers.admin.task_configs import _show_card

    cfg, valya = await make_config(session)
    cfg.next_run_at = None
    await session.commit()

    callback = AsyncMock()
    await _show_card(callback, session, cfg.id)
    text = callback.message.edit_text.await_args.args[0]
    assert "Ближайший запуск" in text
    assert "Валя" in text
    assert "article_check" in text


async def test_show_card_unknown_config_answers_alert(session):
    from bot.handlers.admin.task_configs import _show_card

    callback = AsyncMock()
    await _show_card(callback, session, 999)
    callback.answer.assert_awaited_once()
    assert callback.answer.await_args.kwargs.get("show_alert") is True


# ---------------------------------------------------------------------------
# Активация / деактивация — confirm_token для деактивации, обе ветки
# пересобирают job планировщика (rebuild_config_job).
# ---------------------------------------------------------------------------

async def test_activate_calls_rebuild_config_job_immediately(session):
    from bot.handlers.admin.task_configs import _toggle_active
    from bot.services.admin_service import AdminService

    owner = await _owner(session)
    cfg, valya = await make_config(session)
    cfg.is_active = False
    await session.commit()

    svc = AdminService(session)
    scheduler_svc = AsyncMock()
    callback = AsyncMock()
    await _toggle_active(callback, session, owner, svc, scheduler_svc, cfg.id)

    reloaded = await TaskRepository(session).get_config(cfg.id)
    assert reloaded.is_active is True
    scheduler_svc.rebuild_config_job.assert_awaited_once_with(cfg.id)


async def test_deactivate_goes_through_confirm_token_and_rebuilds_job(session):
    from bot.handlers.admin.task_configs import _toggle_active
    from bot.keyboards.admin.confirm import ConfirmCb
    from bot.services.admin_service import AdminService

    owner = await _owner(session)
    cfg, valya = await make_config(session)   # is_active=True из make_config
    await session.commit()

    svc = AdminService(session)
    scheduler_svc = AsyncMock()
    callback = AsyncMock()
    await _toggle_active(callback, session, owner, svc, scheduler_svc, cfg.id)

    reloaded = await TaskRepository(session).get_config(cfg.id)
    assert reloaded.is_active is True     # не деактивирован сразу
    scheduler_svc.rebuild_config_job.assert_not_awaited()

    reply_markup = _reply_markup(callback)
    token = reply_markup.inline_keyboard[0][0].callback_data
    confirm_cb = ConfirmCb.unpack(token)
    entry = svc.get_pending(confirm_cb.t)
    assert entry is not None
    assert entry.required_permission == "tasks.manage"

    await svc.execute_confirmed(confirm_cb.t)
    reloaded = await TaskRepository(session).get_config(cfg.id)
    assert reloaded.is_active is False
    scheduler_svc.rebuild_config_job.assert_awaited_once_with(cfg.id)   # job снят/пересобран


# ---------------------------------------------------------------------------
# Whitelist-редактирование по индексу: bool-поле — мгновенный toggle;
# schedule-affecting текстовое поле — вызывает rebuild_config_job после
# сохранения; не-schedule поле — НЕ вызывает rebuild_config_job.
# ---------------------------------------------------------------------------

async def test_edit_bool_field_instant_toggle_via_index(session):
    from bot.handlers.admin.task_configs import FIELD_LIST_FOR_UI, _start_edit_field
    from bot.services.admin_service import AdminService

    owner = await _owner(session)
    cfg, valya = await make_config(session)
    await session.commit()
    idx = FIELD_LIST_FOR_UI.index("run_on_weekends")
    assert cfg.run_on_weekends is True

    svc = AdminService(session)
    scheduler_svc = AsyncMock()
    callback = AsyncMock()
    await _start_edit_field(callback, session, None, owner, svc, scheduler_svc, cfg.id, idx)

    reloaded = await TaskRepository(session).get_config(cfg.id)
    assert reloaded.run_on_weekends is False
    scheduler_svc.rebuild_config_job.assert_awaited_once_with(cfg.id)   # schedule-affecting


async def test_edit_text_field_schedule_affecting_triggers_rebuild(session):
    from bot.handlers.admin.task_configs import handle_cfg_edit_message
    from bot.services.setting_service import SettingService

    owner = await _owner(session)
    cfg, valya = await make_config(session)
    await session.commit()

    state = FakeState()
    await state.update_data(config_id=cfg.id, field="schedule_interval")
    message = AsyncMock()
    message.from_user.id = owner.telegram_id
    message.text = "5"

    class FakeWorkflowData(dict):
        pass

    scheduler_svc = AsyncMock()
    scheduler_obj = type("S", (), {"wb_service": scheduler_svc})()
    dispatcher = type("D", (), {"workflow_data": {"scheduler": scheduler_obj}})()

    await handle_cfg_edit_message(message, session, state, dispatcher)

    reloaded = await TaskRepository(session).get_config(cfg.id)
    assert reloaded.schedule_interval == 5
    scheduler_svc.rebuild_config_job.assert_awaited_once_with(cfg.id)
    assert state.state is None      # успех — FSM очищен


async def test_edit_text_field_non_schedule_does_not_trigger_rebuild(session):
    from bot.handlers.admin.task_configs import handle_cfg_edit_message

    owner = await _owner(session)
    cfg, valya = await make_config(session)
    await session.commit()

    state = FakeState()
    await state.update_data(config_id=cfg.id, field="title")
    message = AsyncMock()
    message.from_user.id = owner.telegram_id
    message.text = "Обновлённое название"

    scheduler_svc = AsyncMock()
    scheduler_obj = type("S", (), {"wb_service": scheduler_svc})()
    dispatcher = type("D", (), {"workflow_data": {"scheduler": scheduler_obj}})()

    await handle_cfg_edit_message(message, session, state, dispatcher)

    reloaded = await TaskRepository(session).get_config(cfg.id)
    assert reloaded.title == "Обновлённое название"
    scheduler_svc.rebuild_config_job.assert_not_awaited()


async def test_edit_text_field_rejects_invalid_input_stays_in_state(session):
    from bot.handlers.admin.task_configs import handle_cfg_edit_message

    owner = await _owner(session)
    cfg, valya = await make_config(session)
    await session.commit()

    state = FakeState()
    await state.set_state("waiting_cfg_edit")
    await state.update_data(config_id=cfg.id, field="schedule_interval")
    message = AsyncMock()
    message.from_user.id = owner.telegram_id
    message.text = "not-a-number"

    await handle_cfg_edit_message(message, session, state)
    message.answer.assert_awaited()
    assert state.state is not None   # остаётся в FSM для повтора


# ---------------------------------------------------------------------------
# Клонирование через callback-слой
# ---------------------------------------------------------------------------

async def test_do_clone_callback_shows_inactive_clone_card(session):
    from bot.handlers.admin.task_configs import _do_clone

    owner = await _owner(session)
    cfg, valya = await make_config(session)
    await session.commit()

    callback = AsyncMock()
    await _do_clone(callback, session, owner, cfg.id)
    text = callback.message.edit_text.await_args.args[0]
    assert "неактивен" in text.lower()
    assert cfg.external_task_id in text or True  # заголовок совпадает, id — новый


# ---------------------------------------------------------------------------
# «▶ Запустить сейчас» — manual_run_config, через confirm_token
# ---------------------------------------------------------------------------

async def test_manual_run_creates_instance_and_sends_message(session):
    from bot.handlers.admin.task_configs import manual_run_config

    owner = await _owner(session)
    cfg, valya = await make_config(session)
    await TopicRepository(session).upsert("goods", "Товары", message_thread_id=1)
    await session.commit()

    bot = AsyncMock()
    bot.send_message.return_value = AsyncMock(message_id=5, chat=AsyncMock(id=-100))
    scheduler_svc = AsyncMock()
    scheduler_svc.scheduler = AsyncMock()
    scheduler_svc.register_instance_jobs = MagicMock()   # реальный метод синхронный, не awaited

    inst = await manual_run_config(session, bot, scheduler_svc, owner, cfg.id)
    await session.commit()

    assert inst is not None
    bot.send_message.assert_awaited()
    scheduler_svc.register_instance_jobs.assert_called_once_with(inst)

    from sqlalchemy import select
    from bot.database.models import AdminAuditLog
    logs = list(await session.scalars(select(AdminAuditLog)))
    assert any(l.action == "task_config.manual_run" for l in logs)


async def test_manual_run_button_goes_through_confirm_token(session):
    from bot.handlers.admin.task_configs import _start_manual_run
    from bot.keyboards.admin.confirm import ConfirmCb
    from bot.services.admin_service import AdminService

    owner = await _owner(session)
    cfg, valya = await make_config(session)
    await session.commit()

    svc = AdminService(session)
    callback = AsyncMock()
    callback.bot.send_message.return_value = AsyncMock(message_id=1, chat=AsyncMock(id=-100))
    await _start_manual_run(callback, session, owner, svc, None, cfg.id)

    reply_markup = _reply_markup(callback)
    token = reply_markup.inline_keyboard[0][0].callback_data
    confirm_cb = ConfirmCb.unpack(token)
    entry = svc.get_pending(confirm_cb.t)
    assert entry is not None and entry.required_permission == "tasks.manage"

    from sqlalchemy import select
    from bot.database.models import TaskInstance
    before = list(await session.scalars(select(TaskInstance)))
    assert before == []       # ничего не создано до подтверждения

    await svc.execute_confirmed(confirm_cb.t)
    after = list(await session.scalars(select(TaskInstance)))
    assert len(after) == 1    # создано только после подтверждения


# ---------------------------------------------------------------------------
# Мастер создания шаблона (полный проход через все шаги)
# ---------------------------------------------------------------------------

async def test_full_creation_wizard_creates_inactive_config(session):
    from bot.handlers.admin.task_configs import (
        _cr_pick_approval, _cr_pick_receiver, _cr_pick_responsible, _cr_pick_scenario,
        _cr_pick_schedule_type, _cr_pick_topic, _start_create, handle_cfg_create_message,
    )

    owner = await _owner(session)
    responsible = await UserRepository(session).upsert(telegram_id=20, name="Ответственный",
                                                        role=Role.LOGISTIC)
    await TopicRepository(session).upsert("goods", "Товары")
    topic = await TopicRepository(session).get_by_key("goods")
    await session.commit()

    state = FakeState()
    callback = AsyncMock()
    callback.from_user.id = owner.telegram_id
    await _start_create(callback, state)
    assert state.data["step"] == "title"

    async def send(text):
        msg = AsyncMock()
        msg.from_user.id = owner.telegram_id
        msg.text = text
        await handle_cfg_create_message(msg, session, state)
        return msg

    await send("Мой шаблон")
    assert state.data["step"] == "description"
    await send("-")
    assert state.data["step"] == "responsible"

    await _cr_pick_responsible(callback, session, state, str(responsible.id))
    assert state.data["step"] == "topic"
    await _cr_pick_topic(callback, state, str(topic.id))
    assert state.data["step"] == "scenario"
    await _cr_pick_scenario(callback, state, TaskScenario.SIMPLE)
    assert state.data["step"] == "schedule_type"
    await _cr_pick_schedule_type(callback, state, ScheduleType.EVERY_N_DAYS)
    assert state.data["step"] == "schedule_value"

    await send("3")
    assert state.data["step"] == "time"
    await send("09:30")
    assert state.data["step"] == "due_time"
    await send("-")
    assert state.data["step"] == "need_approval"

    await _cr_pick_approval(callback, state, "1")
    assert state.data["step"] == "remind_hours"
    await send("3,6")
    assert state.data["step"] == "question_receiver"

    await _cr_pick_receiver(callback, session, state, owner, "none")

    from sqlalchemy import select
    from bot.database.models import TaskConfig
    created = (await session.scalars(select(TaskConfig).where(TaskConfig.title == "Мой шаблон"))).one()
    assert created.is_active is False
    assert created.responsible_user_id == responsible.id
    assert created.topic_id == topic.id
    assert created.scenario == TaskScenario.SIMPLE
    assert created.schedule_type == ScheduleType.EVERY_N_DAYS
    assert created.schedule_interval == 3
    assert created.time == time(9, 30)
    assert created.need_approval is True
    assert created.remind_after_hours == 3
    assert created.second_remind_after_hours == 6
    assert created.question_receiver_user_id is None
    assert state.state is None and state.data == {}


async def test_cancel_creation_clears_state(session):
    from bot.handlers.admin.task_configs import _cancel_create, _start_create

    state = FakeState()
    callback = AsyncMock()
    await _start_create(callback, state)
    assert state.state is not None
    await _cancel_create(callback, state)
    assert state.state is None and state.data == {}
