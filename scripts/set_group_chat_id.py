"""Разовый бутстрап: прописать general.group_chat_id напрямую в БД.

Используется когда владелец узнал chat_id через /start в группе (см.
bot/handlers/start.py), но ещё не настроил остальной /admin-доступ.

Запуск: python scripts/set_group_chat_id.py <chat_id>
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.config import get_settings
from bot.database.models import User
from bot.services.setting_service import SettingService


async def main(chat_id: int) -> None:
    settings = get_settings()
    engine = create_async_engine(settings.database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        owner = (await session.execute(
            select(User).where(User.telegram_id == 910256253))).scalars().first()
        svc = SettingService(session)
        await svc.set("general.group_chat_id", chat_id, owner.id if owner else None)
        await session.commit()
        print("OK, general.group_chat_id =", await svc.get("general.group_chat_id"))
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main(int(sys.argv[1])))
