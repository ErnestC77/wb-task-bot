from bot.database.repositories.user_repository import UserRepository


async def test_upsert_sets_private_chat_available_on_insert(session_factory):
    async with session_factory() as s:
        repo = UserRepository(s)
        user = await repo.upsert(111, "Аня", "manager_wb", private_chat_available=True)
        await s.commit()
        assert user.private_chat_available is True


async def test_upsert_without_flag_does_not_reset_existing_value(session_factory):
    """Sheets-синк (bot/services/google_sheets_service.py) вызывает upsert без
    этого параметра — апдейт не должен молча сбрасывать значение, выставленное
    где-то ещё (например, когда пользователь запускает /start в личке)."""
    async with session_factory() as s:
        repo = UserRepository(s)
        user = await repo.upsert(222, "Боря", "logistic", private_chat_available=True)
        await s.commit()
        await repo.upsert(222, "Боря", "logistic")  # без private_chat_available — как в sync
        await s.commit()
        assert user.private_chat_available is True


async def test_upsert_explicit_flag_updates_existing_value(session_factory):
    async with session_factory() as s:
        repo = UserRepository(s)
        user = await repo.upsert(333, "Вера", "owner", private_chat_available=False)
        await s.commit()
        await repo.upsert(333, "Вера", "owner", private_chat_available=True)
        await s.commit()
        assert user.private_chat_available is True
