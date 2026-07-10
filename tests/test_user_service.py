from bot.database.models import Role
from bot.database.repositories.user_repository import UserRepository
from bot.services.user_service import UserService


async def test_get_actor_returns_active_user(session):
    users = UserRepository(session)
    await users.upsert(telegram_id=10, name="Валя", role=Role.MANAGER_WB)
    await session.commit()

    svc = UserService(session)
    actor = await svc.get_actor(10)
    assert actor is not None
    assert actor.telegram_id == 10
    assert actor.name == "Валя"


async def test_get_actor_returns_none_for_unknown_telegram_id(session):
    svc = UserService(session)
    actor = await svc.get_actor(999)
    assert actor is None


async def test_get_actor_returns_none_for_deactivated_user(session):
    users = UserRepository(session)
    await users.upsert(telegram_id=10, name="Валя", role=Role.MANAGER_WB, is_active=False)
    await session.commit()

    svc = UserService(session)
    actor = await svc.get_actor(10)
    assert actor is None


async def test_upsert_from_telegram_creates_new_user(session):
    svc = UserService(session)
    user = await svc.upsert_from_telegram(telegram_id=20, name="Ася", role=Role.OWNER,
                                          username="asya")
    await session.commit()

    assert user.telegram_id == 20
    assert user.name == "Ася"
    assert user.role == Role.OWNER
    assert user.username == "asya"

    users = UserRepository(session)
    all_users = await users.get_all()
    assert len([u for u in all_users if u.telegram_id == 20]) == 1


async def test_upsert_from_telegram_updates_existing_user_without_duplicate(session):
    svc = UserService(session)
    first = await svc.upsert_from_telegram(telegram_id=20, name="Ася", role=Role.OWNER,
                                           username="asya")
    await session.commit()

    second = await svc.upsert_from_telegram(telegram_id=20, name="Ася Новая",
                                            role=Role.PARTNER, username="asya_new")
    await session.commit()

    assert second.id == first.id
    assert second.name == "Ася Новая"
    assert second.role == Role.PARTNER
    assert second.username == "asya_new"

    users = UserRepository(session)
    all_users = await users.get_all()
    assert len([u for u in all_users if u.telegram_id == 20]) == 1


async def test_mark_private_chat_available_sets_flag_true(session):
    users = UserRepository(session)
    user = await users.upsert(telegram_id=30, name="Кирилл", role=Role.MANAGER_WB)
    await session.commit()
    assert user.private_chat_available is False

    svc = UserService(session)
    await svc.mark_private_chat_available(30)
    await session.commit()

    refreshed = await users.get_by_telegram_id(30)
    assert refreshed.private_chat_available is True


async def test_mark_private_chat_available_noop_for_unknown_telegram_id(session):
    svc = UserService(session)
    # Не должно бросать исключение — текущая реализация молча ничего не делает.
    await svc.mark_private_chat_available(999)

    users = UserRepository(session)
    assert await users.get_by_telegram_id(999) is None
