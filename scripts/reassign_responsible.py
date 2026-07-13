"""Разовая диагностика: снять тестовую подмену "всё на владельца" и
назначить реальных ответственных по шаблонам задач (tasks_config):
тема "sales" -> Валя (manager_wb), тема "logistics" -> Оксана (logistic).

Запуск: python scripts/reassign_responsible.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.config import get_settings
from bot.database.models import TaskConfig, Topic, User


async def main() -> None:
    settings = get_settings()
    engine = create_async_engine(settings.database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        valya = (await session.execute(
            select(User).where(User.telegram_id == 428913297))).scalars().first()
        oksana = (await session.execute(
            select(User).where(User.telegram_id == 5973953481))).scalars().first()
        topics = {t.topic_key: t.id for t in
                  (await session.execute(select(Topic))).scalars().all()}

        assignment = {topics.get("sales"): valya, topics.get("logistics"): oksana}
        configs = (await session.execute(
            select(TaskConfig).where(TaskConfig.is_active == True))).scalars().all()  # noqa: E712
        for cfg in configs:
            user = assignment.get(cfg.topic_id)
            if user is not None:
                cfg.responsible_user_id = user.id
                print(f"{cfg.external_task_id} -> {user.name}")
            else:
                print(f"{cfg.external_task_id}: topic_id={cfg.topic_id} not in assignment, skipped")
        await session.commit()
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
