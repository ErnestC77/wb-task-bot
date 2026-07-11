from unittest.mock import AsyncMock

from bot.database.models import Role
from bot.database.repositories.user_repository import UserRepository
from bot.services.admin_service import SECTION_PERMISSIONS, AdminService
from bot.services.permission_service import PermissionService


async def _users(session):
    repo = UserRepository(session)
    owner = await repo.upsert(telegram_id=1, name="O", role=Role.OWNER)
    partner = await repo.upsert(telegram_id=2, name="P", role=Role.PARTNER)
    manager = await repo.upsert(telegram_id=3, name="M", role=Role.MANAGER_WB)
    logistic = await repo.upsert(telegram_id=4, name="L", role=Role.LOGISTIC)
    return owner, partner, manager, logistic


async def test_owner_sees_all_sections(session):               # тест 1
    owner, *_ = await _users(session)
    svc = AdminService(session)
    assert await svc.visible_sections(owner) == set(SECTION_PERMISSIONS)


async def test_partner_sees_only_granted(session):             # тест 2
    owner, partner, *_ = await _users(session)
    perms = PermissionService(session)
    await perms.grant(owner, partner.id, "reports.manage")
    await perms.grant(owner, partner.id, "sync.run")
    svc = AdminService(session)
    assert await svc.visible_sections(partner) == {"rep", "syn"}


async def test_manager_and_logistic_denied(session):           # тест 3
    _, _, manager, logistic = await _users(session)
    svc = AdminService(session)
    assert await svc.visible_sections(manager) == set()
    assert await svc.visible_sections(logistic) == set()


async def test_admin_command_denied_for_manager(session):
    _, _, manager, _ = await _users(session)
    from bot.handlers.admin.main import cmd_admin
    message = AsyncMock()
    message.from_user.id = manager.telegram_id
    await cmd_admin(message, session)
    assert "запрещен" in message.answer.await_args.args[0].lower()


async def test_unknown_user_callback_rejected(session):        # тесты 22, 23
    await _users(session)
    from bot.handlers.admin.main import handle_section
    from bot.keyboards.admin.main import AdminCb
    callback = AsyncMock()
    callback.from_user.id = 999999                             # не зарегистрирован
    await handle_section(callback, AdminCb(s="set"), session)
    callback.answer.assert_awaited()
    assert "прав" in callback.answer.await_args.args[0].lower()


async def test_dangerous_operation_requires_confirm(session):  # тесты 8, 24
    owner, *_ = await _users(session)
    svc = AdminService(session)
    executed = []

    async def op():
        executed.append(1)

    token = svc.confirm_token("test.op", op, required_permission="settings.manage",
                              creator_actor_id=owner.id)
    assert executed == []                                      # без подтверждения — нет
    await svc.execute_confirmed(token)
    assert executed == [1]
    # повторное подтверждение того же токена идемпотентно (токен одноразовый)
    await svc.execute_confirmed(token)
    assert executed == [1]


# ---------------------------------------------------------------------------
# Fix: confirm_token authorization, DM routing, TTL (Task 23 review — Critical
# + Important #1-3). См. .superpowers/sdd/task-23-report.md, раздел
# "Fix: confirm_token authorization, DM routing, TTL".
# ---------------------------------------------------------------------------

async def test_handle_confirm_rejects_registered_user_without_required_permission(session):
    """Critical exploit regression: до фикса handle_confirm проверял только
    actor is None — ЛЮБОЙ зарегистрированный активный пользователь (в т.ч.
    LOGISTIC с нулём прав в системе) мог подтвердить кнопкой ЧУЖУЮ
    привилегированную операцию, созданную OWNER. Теперь required_permission,
    привязанное к токену, обязано быть у того, кто жмёт «Подтвердить»."""
    owner, _, _, logistic = await _users(session)
    from bot.handlers.admin.main import handle_confirm
    from bot.keyboards.admin.confirm import ConfirmCb

    svc = AdminService(session)
    executed = []

    async def dangerous_op():
        executed.append("wiped")

    token = svc.confirm_token("danger.op", dangerous_op,
                              required_permission="settings.manage",
                              creator_actor_id=owner.id)

    callback = AsyncMock()
    callback.from_user.id = logistic.telegram_id            # 0 прав в системе
    await handle_confirm(callback, ConfirmCb(t=token, ok=True), session)

    assert executed == []                                     # операция НЕ выполнена
    callback.answer.assert_awaited()
    args, kwargs = callback.answer.await_args
    assert "прав" in args[0].lower()
    assert kwargs.get("show_alert") is True
    callback.message.edit_text.assert_not_awaited()            # не притворились "выполнено"

    # токен всё ещё жив — авторизованный пользователь сможет подтвердить позже
    assert svc.get_pending(token) is not None


async def test_handle_confirm_allows_different_user_with_same_permission(session):
    """Решение по «кто может подтверждать»: подтвердить может ЛЮБОЙ пользователь
    с required_permission — не обязательно тот, кто инициировал (creator_actor_id).
    Это обычная практика двойного контроля для UI «Вы уверены? ✅/❌»."""
    owner, partner, *_ = await _users(session)
    perms = PermissionService(session)
    await perms.grant(owner, partner.id, "settings.manage")
    await session.commit()

    from bot.handlers.admin.main import handle_confirm
    from bot.keyboards.admin.confirm import ConfirmCb

    svc = AdminService(session)
    executed = []

    async def op():
        executed.append(1)

    token = svc.confirm_token("test.op", op, required_permission="settings.manage",
                              creator_actor_id=owner.id)    # инициировал owner

    callback = AsyncMock()
    callback.from_user.id = partner.telegram_id             # подтверждает НЕ owner
    await handle_confirm(callback, ConfirmCb(t=token, ok=True), session)

    assert executed == [1]                                    # выполнено


async def test_handle_confirm_cancel_discards_token(session):
    owner, *_ = await _users(session)
    from bot.handlers.admin.main import handle_confirm
    from bot.keyboards.admin.confirm import ConfirmCb

    svc = AdminService(session)
    executed = []

    async def op():
        executed.append(1)

    token = svc.confirm_token("test.op", op, required_permission="settings.manage",
                              creator_actor_id=owner.id)

    callback = AsyncMock()
    callback.from_user.id = owner.telegram_id
    await handle_confirm(callback, ConfirmCb(t=token, ok=False), session)   # ❌ Отмена

    assert executed == []
    assert svc.get_pending(token) is None                     # токен инвалидирован


async def test_confirm_token_expires_after_ttl(session):
    """AdminService уровень: искусственно состариваем токен за TTL и убеждаемся,
    что execute_confirmed отказывается его выполнить."""
    from bot.services import admin_service as admin_service_module

    owner, *_ = await _users(session)
    svc = AdminService(session)
    executed = []

    async def op():
        executed.append(1)

    token = svc.confirm_token("test.op", op, required_permission="settings.manage",
                              creator_actor_id=owner.id)
    entry = admin_service_module._pending_confirms[token]
    entry.created_at -= admin_service_module.CONFIRM_TTL_SECONDS + 1
    assert svc.is_expired(entry)

    ok = await svc.execute_confirmed(token)
    assert ok is False
    assert executed == []


async def test_handle_confirm_rejects_expired_token(session):
    """Handler-уровень: истёкший токен отклоняется с понятным сообщением, а не
    молча выполняется/не молча считается «уже выполненным»."""
    from bot.handlers.admin.main import handle_confirm
    from bot.keyboards.admin.confirm import ConfirmCb
    from bot.services import admin_service as admin_service_module

    owner, *_ = await _users(session)
    svc = AdminService(session)
    executed = []

    async def op():
        executed.append(1)

    token = svc.confirm_token("test.op", op, required_permission="settings.manage",
                              creator_actor_id=owner.id)
    entry = admin_service_module._pending_confirms[token]
    entry.created_at -= admin_service_module.CONFIRM_TTL_SECONDS + 1

    callback = AsyncMock()
    callback.from_user.id = owner.telegram_id
    await handle_confirm(callback, ConfirmCb(t=token, ok=True), session)

    assert executed == []
    text = callback.message.edit_text.await_args.args[0]
    assert "истекло" in text.lower()


async def test_admin_command_sends_menu_to_dm_from_group(session):        # Important #1
    owner, *_ = await _users(session)
    from bot.handlers.admin.main import cmd_admin

    message = AsyncMock()
    message.from_user.id = owner.telegram_id
    message.chat.type = "group"
    await cmd_admin(message, session)

    message.bot.send_message.assert_awaited_once()
    kwargs = message.bot.send_message.await_args.kwargs
    assert kwargs["chat_id"] == owner.telegram_id
    assert "Админ-панель" in kwargs["text"]
    # групповой чат получает короткое уведомление, а не само меню
    message.answer.assert_awaited_once()
    assert "личные сообщения" in message.answer.await_args.args[0].lower()


async def test_admin_command_private_chat_no_extra_notice(session):
    owner, *_ = await _users(session)
    from bot.handlers.admin.main import cmd_admin

    message = AsyncMock()
    message.from_user.id = owner.telegram_id
    message.chat.type = "private"
    await cmd_admin(message, session)

    message.bot.send_message.assert_awaited_once()
    message.answer.assert_not_awaited()                        # без дублирующего уведомления


async def test_admin_command_dm_failure_notifies_group_without_leaking_menu(session):
    owner, *_ = await _users(session)
    from bot.handlers.admin.main import cmd_admin

    message = AsyncMock()
    message.from_user.id = owner.telegram_id
    message.chat.type = "group"
    message.bot.send_message.side_effect = Exception("Forbidden: bot was blocked by the user")
    await cmd_admin(message, session)

    message.answer.assert_awaited_once()
    text = message.answer.await_args.args[0]
    assert "личные сообщения" in text.lower()
    assert "Админ-панель" not in text                          # содержимое меню не утекло


async def test_partner_with_reports_manage_denied_users_section(session):  # Important #2
    owner, partner, *_ = await _users(session)
    perms = PermissionService(session)
    await perms.grant(owner, partner.id, "reports.manage")     # только это право
    await session.commit()

    import bot.handlers.admin as admin_pkg
    from bot.handlers.admin.main import handle_section
    from bot.keyboards.admin.main import AdminCb

    calls = []

    async def fake_users_handler(*args, **kwargs):
        calls.append(args)

    admin_pkg.SECTION_HANDLERS["usr"] = fake_users_handler
    try:
        callback = AsyncMock()
        callback.from_user.id = partner.telegram_id
        await handle_section(callback, AdminCb(s="usr"), session)   # Пользователи и роли
    finally:
        admin_pkg.SECTION_HANDLERS.pop("usr", None)

    assert calls == []                                          # раздел НЕ выполнился
    callback.answer.assert_awaited()
    assert "прав" in callback.answer.await_args.args[0].lower()


async def test_partner_with_reports_manage_denied_run_alias(session):      # Important #2
    owner, partner, *_ = await _users(session)
    perms = PermissionService(session)
    await perms.grant(owner, partner.id, "reports.manage")     # только это право
    await session.commit()

    import bot.handlers.admin as admin_pkg
    from bot.handlers.admin.main import handle_section
    from bot.keyboards.admin.main import AdminCb

    calls = []

    async def fake_run_handler(*args, **kwargs):
        calls.append(args)

    admin_pkg.SECTION_HANDLERS["run"] = fake_run_handler
    try:
        callback = AsyncMock()
        callback.from_user.id = partner.telegram_id
        await handle_section(callback, AdminCb(s="run"), session)   # алиас → ops, не выдано
    finally:
        admin_pkg.SECTION_HANDLERS.pop("run", None)

    assert calls == []                                          # раздел НЕ выполнился
    callback.answer.assert_awaited()
    assert "прав" in callback.answer.await_args.args[0].lower()


async def test_cancel_clears_fsm():                            # тест 9
    from bot.handlers.start import cmd_cancel
    state = AsyncMock()
    message = AsyncMock()
    await cmd_cancel(message, state)
    state.clear.assert_awaited_once()


def test_pagination_keyboard():                                # тест 21
    from bot.keyboards.admin.pagination import pagination_row
    row = pagination_row("usr", page=2, total_pages=5)
    texts = [b.text for b in row]
    assert "⬅" in texts[0] and "2/5" in texts[1] and "➡" in texts[2]
