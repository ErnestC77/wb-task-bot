"""Разовая диагностика: состояние приватного чата владельца и последних
вопросов (TaskQuestion) — статус доставки, delivery-ошибка."""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.config import get_settings
from bot.database.models import TaskQuestion, User


async def main() -> None:
    settings = get_settings()
    engine = create_async_engine(settings.database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        owner = (await session.execute(
            select(User).where(User.telegram_id == 910256253))).scalars().first()
        print("owner private_chat_available =", owner.private_chat_available)

        questions = (await session.execute(
            select(TaskQuestion).order_by(TaskQuestion.id.desc()).limit(10)
        )).scalars().all()
        for q in questions:
            print(f"id={q.id} from={q.from_user_id} to={q.to_user_id} "
                  f"status={q.status} delivery_status={q.delivery_status} "
                  f"error={q.last_delivery_error!r} answer={q.answer_text!r}")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
