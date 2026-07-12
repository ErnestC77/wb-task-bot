"""Разовый бутстрап: прописать questions.default_receiver_user_id (владелец).

Без этой настройки QuestionService.resolve_receiver() не находит ни одного
активного получателя и поднимает ValueError — вопросы сотрудников
("Есть вопрос") никуда не уходят.

Запуск: python scripts/set_question_receiver.py
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


async def main() -> None:
    settings = get_settings()
    engine = create_async_engine(settings.database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        owner = (await session.execute(
            select(User).where(User.telegram_id == 910256253))).scalars().first()
        svc = SettingService(session)
        await svc.set("questions.default_receiver_user_id", owner.id, owner.id)
        await svc.set("questions.fallback_receiver_user_id", owner.id, owner.id)
        await session.commit()
        print("OK, default_receiver_user_id =",
              await svc.get("questions.default_receiver_user_id"))
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
