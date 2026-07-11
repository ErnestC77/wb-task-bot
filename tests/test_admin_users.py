from datetime import datetime
from unittest.mock import AsyncMock

import pytest

from bot.database.models import Role, TaskStatus
from bot.database.repositories.task_repository import TaskRepository
from bot.database.repositories.user_repository import UserRepository
from bot.services.task_service import TaskService
from tests.test_task_service import make_config


class FakeState:
    """Лёгкий дубль FSMContext для тестов — хранит state/data в памяти,
    поддерживает только методы, которые реально используются handlers'ами
    (set_state, update_data, get_data, clear)."""

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


async def _owner(session):
    return await UserRepository(session).upsert(telegram_id=1, name="O", role=Role.OWNER)


def _reply_markup(callback):
    return callback.message.edit_text.await_args.kwargs.get("reply_markup") \
        or callback.message.edit_text.await_args.args[1]


# ---------------------------------------------------------------------------
# Step 1 (брифовые тесты, дословно)
# ---------------------------------------------------------------------------

async def test_question_receiver_requires_active_and_private_chat(session):
    from bot.handlers.admin.users import set_question_receiver
    users = UserRepository(session)
    owner = await users.upsert(telegram_id=1, name="O", role=Role.OWNER)
    target = await users.upsert(telegram_id=2, name="P", role=Role.PARTNER)
    with pytest.raises(ValueError):
        await set_question_receiver(session, owner, target.id)
    target.private_chat_available = True
    await set_question_receiver(session, owner, target.id)
    await users.deactivate(target.id)
    with pytest.raises(ValueError):
        await set_question_receiver(session, owner, target.id)


async def test_reassign_open_task_updates_responsible(session):
    cfg, valya = await make_config(session)
    owner = await UserRepository(session).upsert(telegram_id=1, name="O", role=Role.OWNER)
    petya = await UserRepository(session).upsert(telegram_id=20, name="Петя",
                                                 role=Role.MANAGER_WB)
    inst = await TaskService(session).create_instance_for(cfg, datetime(2026, 7, 10, 9))
    await session.commit()

    from bot.handlers.admin.users import reassign_task
    got = await reassign_task(session, owner, inst.id, petya.id)
    await session.commit()
    assert got.responsible_user_id == petya.id
    assert got.responsible_name_snapshot == "Петя"       # snapshot обновлён явно


async def test_reassign_closed_task_rejected(session):
    cfg, valya = await make_config(session)
    owner = await UserRepository(session).upsert(telegram_id=1, name="O", role=Role.OWNER)
    petya = await UserRepository(session).upsert(telegram_id=20, name="Петя",
                                                 role=Role.MANAGER_WB)
    inst = await TaskService(session).create_instance_for(cfg, datetime(2026, 7, 10, 9))
    await TaskRepository(session).transition_status(
        inst.id, [TaskStatus.CREATED], TaskStatus.CANCELLED, owner.id, "admin:cancel")
    await session.commit()

    from bot.handlers.admin.users import reassign_task
    with pytest.raises(ValueError):
        await reassign_task(session, owner, inst.id, petya.id)


async def test_permission_checklist_lists_all_keys(session):
    from bot.handlers.admin.users import render_permission_checklist
    owner = await UserRepository(session).upsert(telegram_id=1, name="O", role=Role.OWNER)
    partner = await UserRepository(session).upsert(telegram_id=2, name="P", role=Role.PARTNER)
    text = await render_permission_checklist(session, partner.id)
    assert "settings.manage" in text and "audit.view" in text


# ---------------------------------------------------------------------------
# Regression (Task 24 lesson): каждая НОВАЯ клавиатура с составным значением
# на кнопке обязана быть проверена реальным .pack()/.unpack(), а не только
# юнит-тестами бизнес-логики. Ни одно поле UsrCb НЕ должно содержать ":" —
# каждое семантическое значение (user_id, индекс права, id задачи) уходит в
# СВОЁ типизированное поле, а не склеивается строкой.
# ---------------------------------------------------------------------------

def test_users_list_keyboard_pack_unpack_roundtrip_many_users_and_pagination():
    """Много пользователей (двузначные/многозначные id, включая реалистичные
    Telegram id) + пагинация — раньше именно такой сценарий (много строк с
    составным значением) ломал .pack() в разделе "Настройки" (Task 24)."""
    from bot.keyboards.admin.main import AdminCb
    from bot.keyboards.admin.users import UsrCb, users_list_keyboard

    entries = [(i, f"Пользователь {i} (неактивен)" if i % 2 else f"Пользователь {i}")
               for i in range(1, 16)]                      # 15 пользователей, id 1..15
    kb = users_list_keyboard(entries, page=2, total_pages=2)

    user_rows = kb.inline_keyboard[:15]
    assert len(user_rows) == 15
    for idx, row in enumerate(user_rows):
        cb = UsrCb.unpack(row[0].callback_data)             # реальный round-trip
        assert cb.a == "card"
        assert cb.id == entries[idx][0]

    add_row = kb.inline_keyboard[15]
    add_cb = UsrCb.unpack(add_row[0].callback_data)
    assert add_cb.a == "add"

    pag_row = kb.inline_keyboard[16]
    prev_cb = UsrCb.unpack(pag_row[0].callback_data)
    next_cb = UsrCb.unpack(pag_row[2].callback_data)
    assert prev_cb.a == "list" and prev_cb.p == 1
    assert next_cb.a == "list" and next_cb.p == 2

    back_cb = AdminCb.unpack(kb.inline_keyboard[-1][0].callback_data)
    assert back_cb.s == "usr" and back_cb.a == "menu"


def test_user_card_keyboard_pack_unpack_roundtrip_large_telegram_id():
    from bot.keyboards.admin.users import UsrCb, user_card_keyboard

    large_user_id = 987654321          # реалистичный многозначный id
    kb = user_card_keyboard(large_user_id, is_active=True, show_permissions=True)
    flat = [btn for row in kb.inline_keyboard for btn in row]
    actions = {UsrCb.unpack(btn.callback_data).a: UsrCb.unpack(btn.callback_data)
              for btn in flat}
    assert actions["editname"].id == large_user_id
    assert actions["editun"].id == large_user_id
    assert actions["rolepick"].id == large_user_id
    assert actions["perm"].id == large_user_id
    assert actions["toggle"].id == large_user_id and actions["toggle"].k is None
    assert actions["reassign"].id == large_user_id
    assert actions["checkpm"].id == large_user_id
    assert actions["list"].a == "list"                       # "Назад"

    kb_inactive = user_card_keyboard(large_user_id, is_active=False, show_permissions=False)
    flat_inactive = [btn for row in kb_inactive.inline_keyboard for btn in row]
    assert "🔑 Права" not in [b.text for b in flat_inactive]  # partner/owner only
    toggle_btn = next(b for b in flat_inactive if UsrCb.unpack(b.callback_data).a == "toggle")
    assert toggle_btn.text == "✅ Активировать"


def test_role_picker_keyboard_pack_unpack_roundtrip_add_and_edit_flows():
    from bot.keyboards.admin.users import UsrCb, role_picker_keyboard

    kb_add = role_picker_keyboard(0)                         # add-flow, id=0
    for row in kb_add.inline_keyboard[:-1]:
        cb = UsrCb.unpack(row[0].callback_data)
        assert cb.a == "role" and cb.id == 0
        assert cb.k in {"owner", "partner", "manager_wb", "logistic"}

    kb_edit = role_picker_keyboard(42)                        # edit existing user
    for row in kb_edit.inline_keyboard[:-1]:
        cb = UsrCb.unpack(row[0].callback_data)
        assert cb.a == "role" and cb.id == 42


def test_permission_checklist_keyboard_pack_unpack_roundtrip_all_12_keys():
    """12 прав -> индексы 0..11 (двузначные включены) уходят в UsrCb.k строкой,
    user_id — в UsrCb.id отдельным полем. Именно эта komбинация (два разных
    значения на кнопке) — источник Critical-бага Task 24, если бы они были
    склеены через ":"."""
    from bot.handlers.admin.users import PERMISSION_KEYS_SORTED
    from bot.keyboards.admin.users import UsrCb, permission_checklist_keyboard

    assert len(PERMISSION_KEYS_SORTED) == 12
    perms = {k: (i % 2 == 0) for i, k in enumerate(PERMISSION_KEYS_SORTED)}
    user_id = 777
    kb = permission_checklist_keyboard(user_id, PERMISSION_KEYS_SORTED, perms)
    perm_rows = kb.inline_keyboard[:-1]
    assert len(perm_rows) == 12
    for idx, row in enumerate(perm_rows):
        cb = UsrCb.unpack(row[0].callback_data)
        assert cb.a == "toggle"
        assert cb.id == user_id
        assert cb.k == str(idx)
    back_cb = UsrCb.unpack(kb.inline_keyboard[-1][0].callback_data)
    assert back_cb.a == "card" and back_cb.id == user_id


def test_reassign_task_list_keyboard_pack_unpack_roundtrip_multiple_tasks():
    from bot.keyboards.admin.users import UsrCb, reassign_task_list_keyboard

    user_id = 55
    entries = [(inst_id, f"Задача {inst_id}") for inst_id in (101, 102, 103)]
    kb = reassign_task_list_keyboard(user_id, entries, page=1, total_pages=2)
    task_rows = kb.inline_keyboard[:3]
    for idx, row in enumerate(task_rows):
        cb = UsrCb.unpack(row[0].callback_data)
        assert cb.a == "reassign_pick"
        assert cb.id == entries[idx][0]
        assert cb.k == str(user_id)
    pag_row = kb.inline_keyboard[3]
    next_cb = UsrCb.unpack(pag_row[2].callback_data)
    assert next_cb.a == "reassign" and next_cb.id == user_id and next_cb.p == 2


def test_reassign_target_keyboard_pack_unpack_roundtrip_many_candidates():
    from bot.keyboards.admin.users import UsrCb, reassign_target_keyboard

    instance_id = 999999               # многозначный id задачи
    candidates = [(uid, f"User {uid}") for uid in (2, 20, 200, 2000000001)]
    kb = reassign_target_keyboard(instance_id, origin_user_id=2, candidates=candidates)
    for idx, row in enumerate(kb.inline_keyboard[:-1]):
        cb = UsrCb.unpack(row[0].callback_data)
        assert cb.a == "reassign_do"
        assert cb.id == instance_id
        assert cb.k == str(candidates[idx][0])
    back_cb = UsrCb.unpack(kb.inline_keyboard[-1][0].callback_data)
    assert back_cb.a == "reassign" and back_cb.id == 2


# ---------------------------------------------------------------------------
# Деактивация — только через confirm_token с required_permission="users.manage"
# (не физическое удаление; Global Constraint).
# ---------------------------------------------------------------------------

async def test_deactivate_goes_through_confirm_token_not_immediate(session):
    from bot.handlers.admin.users import _toggle_active
    from bot.keyboards.admin.confirm import ConfirmCb
    from bot.services.admin_service import AdminService

    owner = await _owner(session)
    target = await UserRepository(session).upsert(telegram_id=9, name="Target",
                                                   role=Role.MANAGER_WB)
    await session.commit()

    svc = AdminService(session)
    callback = AsyncMock()
    await _toggle_active(callback, session, owner, svc, target.id)

    # НЕ деактивирован сразу
    reloaded = await UserRepository(session).get_by_id(target.id)
    assert reloaded.is_active is True

    reply_markup = _reply_markup(callback)
    token = reply_markup.inline_keyboard[0][0].callback_data
    confirm_cb = ConfirmCb.unpack(token)
    entry = svc.get_pending(confirm_cb.t)
    assert entry is not None
    assert entry.required_permission == "users.manage"

    await svc.execute_confirmed(confirm_cb.t, session)
    reloaded = await UserRepository(session).get_by_id(target.id)
    assert reloaded.is_active is False                      # деактивирован, не удалён
    assert (await UserRepository(session).get_by_id(target.id)) is not None  # запись жива


async def test_reactivate_is_immediate_no_confirm_needed(session):
    from bot.handlers.admin.users import _toggle_active
    from bot.services.admin_service import AdminService

    owner = await _owner(session)
    target = await UserRepository(session).upsert(telegram_id=9, name="Target",
                                                   role=Role.MANAGER_WB)
    await UserRepository(session).deactivate(target.id)
    await session.commit()

    svc = AdminService(session)
    callback = AsyncMock()
    await _toggle_active(callback, session, owner, svc, target.id)

    reloaded = await UserRepository(session).get_by_id(target.id)
    assert reloaded.is_active is True


# ---------------------------------------------------------------------------
# Owner revoke своего собственного права — отдельный confirm_token, отличный
# от обычного мгновенного toggle (LastOwnerPermissionError -> confirm flow).
# ---------------------------------------------------------------------------

async def test_owner_self_revoke_requires_confirm_token(session):
    from bot.handlers.admin.users import PERMISSION_KEYS_SORTED, _toggle_permission
    from bot.keyboards.admin.confirm import ConfirmCb
    from bot.services.admin_service import AdminService
    from bot.services.permission_service import PermissionService

    owner = await _owner(session)
    await session.commit()
    idx = PERMISSION_KEYS_SORTED.index("settings.manage")

    svc = AdminService(session)
    callback = AsyncMock()
    await _toggle_permission(callback, session, owner, svc, owner.id, str(idx))

    # право ещё при владельце (не снято мгновенно)
    assert await PermissionService(session).has_permission(owner, "settings.manage") is True

    reply_markup = _reply_markup(callback)
    token = reply_markup.inline_keyboard[0][0].callback_data
    confirm_cb = ConfirmCb.unpack(token)
    entry = svc.get_pending(confirm_cb.t)
    assert entry is not None and entry.required_permission == "users.manage"

    await svc.execute_confirmed(confirm_cb.t, session)
    assert await PermissionService(session).has_permission(owner, "settings.manage") is False


async def test_toggle_permission_for_partner_is_immediate(session):
    """Обычный toggle (не self-revoke owner) выполняется сразу через
    PermissionService.grant/revoke — без confirm_token."""
    from bot.handlers.admin.users import PERMISSION_KEYS_SORTED, _toggle_permission
    from bot.services.admin_service import AdminService
    from bot.services.permission_service import PermissionService

    owner = await _owner(session)
    partner = await UserRepository(session).upsert(telegram_id=2, name="P", role=Role.PARTNER)
    await session.commit()
    idx = PERMISSION_KEYS_SORTED.index("reports.manage")

    svc = AdminService(session)
    callback = AsyncMock()
    await _toggle_permission(callback, session, owner, svc, partner.id, str(idx))
    assert await PermissionService(session).has_permission(partner, "reports.manage") is True

    callback2 = AsyncMock()
    await _toggle_permission(callback2, session, owner, svc, partner.id, str(idx))
    assert await PermissionService(session).has_permission(partner, "reports.manage") is False


# ---------------------------------------------------------------------------
# actor-check + users.manage на каждом уровне: UsrCb-callback НЕ проходит
# через resolve_admin (у него свой prefix "u", не "ad") — handle_usr_callback
# обязан сам проверить actor/право.
# ---------------------------------------------------------------------------

async def test_usr_callback_rejects_unauthorized_actor(session):
    from bot.handlers.admin.users import handle_usr_callback
    from bot.keyboards.admin.users import UsrCb

    manager = await UserRepository(session).upsert(telegram_id=3, name="M", role=Role.MANAGER_WB)
    await session.commit()

    callback = AsyncMock()
    callback.from_user.id = manager.telegram_id
    await handle_usr_callback(callback, UsrCb(a="list", p=1), session)

    callback.answer.assert_awaited()
    assert "прав" in callback.answer.await_args.args[0].lower()
    callback.message.edit_text.assert_not_awaited()


async def test_usr_callback_rejects_unknown_actor(session):
    from bot.handlers.admin.users import handle_usr_callback
    from bot.keyboards.admin.users import UsrCb

    callback = AsyncMock()
    callback.from_user.id = 999999999
    await handle_usr_callback(callback, UsrCb(a="card", id=1), session)

    callback.answer.assert_awaited()
    assert "прав" in callback.answer.await_args.args[0].lower()
    callback.message.edit_text.assert_not_awaited()


async def test_edit_message_handler_denies_actor_without_permission(session):
    from bot.handlers.admin.users import handle_edit_name_message

    manager = await UserRepository(session).upsert(telegram_id=3, name="M", role=Role.MANAGER_WB)
    target = await UserRepository(session).upsert(telegram_id=9, name="Old", role=Role.LOGISTIC)
    await session.commit()

    state = FakeState()
    await state.update_data(edit_user_id=target.id)
    message = AsyncMock()
    message.from_user.id = manager.telegram_id
    message.text = "New Name"

    await handle_edit_name_message(message, session, state)

    reloaded = await UserRepository(session).get_by_id(target.id)
    assert reloaded.name == "Old"
    assert "прав" in message.answer.await_args.args[0].lower()


# ---------------------------------------------------------------------------
# Полный happy-path: список -> карточка -> добавление (FSM) -> роль -> карточка
# ---------------------------------------------------------------------------

async def test_show_users_list_marks_inactive_and_paginates(session):
    from bot.handlers.admin.users import _show_users_list

    users_repo = UserRepository(session)
    active = await users_repo.upsert(telegram_id=1, name="Active", role=Role.OWNER)
    inactive = await users_repo.upsert(telegram_id=2, name="Inactive", role=Role.LOGISTIC)
    await users_repo.deactivate(inactive.id)
    await session.commit()

    callback = AsyncMock()
    await _show_users_list(callback, session, 1)

    from bot.keyboards.admin.users import UsrCb
    reply_markup = _reply_markup(callback)
    # последняя строка — «Назад» на AdminCb (другой prefix), остальные — UsrCb
    user_rows = reply_markup.inline_keyboard[:-1]
    labels = {UsrCb.unpack(row[0].callback_data).id: row[0].text
              for row in user_rows
              if UsrCb.unpack(row[0].callback_data).a == "card"}
    assert labels[active.id] == "Active"
    assert labels[inactive.id] == "Inactive (неактивен)"


async def test_full_add_user_flow_via_fsm_and_role_pick(session):
    from bot.handlers.admin.users import (
        _apply_role, _start_add, handle_add_id_message, handle_add_name_message,
    )

    owner = await _owner(session)
    await session.commit()

    state = FakeState()
    callback = AsyncMock()
    callback.from_user.id = owner.telegram_id
    await _start_add(callback, state)
    assert state.state is not None

    id_message = AsyncMock()
    id_message.from_user.id = owner.telegram_id
    id_message.text = "555444333"
    await handle_add_id_message(id_message, session, state)
    assert state.data["new_telegram_id"] == 555444333

    name_message = AsyncMock()
    name_message.from_user.id = owner.telegram_id
    name_message.text = "Новый Партнёр"
    await handle_add_name_message(name_message, session, state)
    assert state.data["new_name"] == "Новый Партнёр"

    role_callback = AsyncMock()
    await _apply_role(role_callback, session, state, owner, 0, "partner")

    created = await UserRepository(session).get_by_telegram_id(555444333)
    assert created is not None
    assert created.name == "Новый Партнёр"
    assert created.role == "partner"
    assert state.data == {} and state.state is None          # FSM очищен после добавления


async def test_add_user_role_pick_without_fsm_data_fails_safely(session):
    """Форжированный callback UsrCb(a="role", id=0, ...) без реального прохода
    через add_id/add_name не должен создавать пользователя."""
    from bot.handlers.admin.users import _apply_role

    owner = await _owner(session)
    await session.commit()
    state = FakeState()
    callback = AsyncMock()

    await _apply_role(callback, session, state, owner, 0, "partner")

    callback.answer.assert_awaited()
    assert "утер" in callback.answer.await_args.args[0].lower()
    assert (await UserRepository(session).get_all(include_inactive=True)) == [owner]


async def test_apply_role_rejects_unknown_role_value(session):
    from bot.handlers.admin.users import _apply_role

    owner = await _owner(session)
    target = await UserRepository(session).upsert(telegram_id=9, name="T", role=Role.LOGISTIC)
    await session.commit()
    callback = AsyncMock()

    await _apply_role(callback, session, None, owner, target.id, "superadmin")

    callback.answer.assert_awaited()
    assert "неизвестн" in callback.answer.await_args.args[0].lower()
    reloaded = await UserRepository(session).get_by_id(target.id)
    assert reloaded.role == "logistic"                        # роль не изменилась


async def test_edit_name_and_username_via_fsm(session):
    from bot.handlers.admin.users import handle_edit_name_message, handle_edit_username_message

    owner = await _owner(session)
    target = await UserRepository(session).upsert(telegram_id=9, name="Old", role=Role.LOGISTIC)
    await session.commit()

    state = FakeState()
    await state.update_data(edit_user_id=target.id)
    name_message = AsyncMock()
    name_message.from_user.id = owner.telegram_id
    name_message.text = "New Name"
    await handle_edit_name_message(name_message, session, state)

    reloaded = await UserRepository(session).get_by_id(target.id)
    assert reloaded.name == "New Name"
    assert state.state is None

    state2 = FakeState()
    await state2.update_data(edit_user_id=target.id)
    un_message = AsyncMock()
    un_message.from_user.id = owner.telegram_id
    un_message.text = "@new_username"
    await handle_edit_username_message(un_message, session, state2)

    reloaded = await UserRepository(session).get_by_id(target.id)
    assert reloaded.username == "new_username"


async def test_check_private_message_updates_flag(session):
    from bot.handlers.admin.users import _check_private_message

    owner = await _owner(session)
    target = await UserRepository(session).upsert(telegram_id=9, name="T", role=Role.LOGISTIC)
    await session.commit()

    callback = AsyncMock()
    callback.bot.send_message = AsyncMock(return_value=AsyncMock(message_id=1))
    await _check_private_message(callback, session, owner, target.id)

    reloaded = await UserRepository(session).get_by_id(target.id)
    assert reloaded.private_chat_available is True


async def test_check_private_message_failure_keeps_flag_false(session):
    from bot.handlers.admin.users import _check_private_message

    owner = await _owner(session)
    target = await UserRepository(session).upsert(telegram_id=9, name="T", role=Role.LOGISTIC)
    await session.commit()

    callback = AsyncMock()
    callback.bot.send_message = AsyncMock(side_effect=Exception("Forbidden"))
    await _check_private_message(callback, session, owner, target.id)

    reloaded = await UserRepository(session).get_by_id(target.id)
    assert reloaded.private_chat_available is False


async def test_reassign_flow_via_callbacks_end_to_end(session):
    from bot.handlers.admin.users import _reassign_do, _reassign_pick, _show_reassign_list
    from bot.keyboards.admin.users import UsrCb

    cfg, valya = await make_config(session)
    owner = await _owner(session)
    petya = await UserRepository(session).upsert(telegram_id=20, name="Петя",
                                                  role=Role.MANAGER_WB)
    inst = await TaskService(session).create_instance_for(cfg, datetime(2026, 7, 10, 9))
    await session.commit()

    callback = AsyncMock()
    await _show_reassign_list(callback, session, valya.id, 1)
    reply_markup = _reply_markup(callback)
    pick_cb = UsrCb.unpack(reply_markup.inline_keyboard[0][0].callback_data)
    assert pick_cb.a == "reassign_pick" and pick_cb.id == inst.id

    callback2 = AsyncMock()
    await _reassign_pick(callback2, session, pick_cb.id, pick_cb.k)
    reply_markup2 = _reply_markup(callback2)
    do_cb = UsrCb.unpack(reply_markup2.inline_keyboard[0][0].callback_data)
    assert do_cb.a == "reassign_do" and do_cb.id == inst.id

    callback3 = AsyncMock()
    await _reassign_do(callback3, session, owner, do_cb.id, do_cb.k)

    reloaded = await TaskRepository(session).get_instance(inst.id)
    # переназначено кому-то из активных пользователей, КРОМЕ исходного
    # ответственного (valya исключена из списка кандидатов _reassign_pick)
    assert reloaded.responsible_user_id != valya.id
    assert reloaded.responsible_user_id in {owner.id, petya.id}
