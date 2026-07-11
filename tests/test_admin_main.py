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

    token = svc.confirm_token("test.op", op)
    assert executed == []                                      # без подтверждения — нет
    await svc.execute_confirmed(token)
    assert executed == [1]
    # повторное подтверждение того же токена идемпотентно (токен одноразовый)
    await svc.execute_confirmed(token)
    assert executed == [1]


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
