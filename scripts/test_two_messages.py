"""Разовая диагностика: отправить N тестовых задач (не все 14), чтобы
проверить фиксы (HTML, упоминание, кнопки) без повторного спама всей группы.

Запуск: python scripts/test_two_messages.py [external_task_id ...]
Без аргументов — 2 задачи по умолчанию (EXTERNAL_IDS).
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.config import get_settings
from bot.database.models import TaskConfig, User
from bot.handlers.admin.task_configs import manual_run_config
from bot.loader import create_bot

EXTERNAL_IDS = ["wb_financial_report", "cpl_high"]


async def main() -> None:
    settings = get_settings()
    engine = create_async_engine(settings.database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    bot = create_bot(settings.bot_token)

    external_ids = sys.argv[1:] or EXTERNAL_IDS
    async with session_factory() as session:
        owner = (await session.execute(
            select(User).where(User.telegram_id == 910256253))).scalars().first()
        for ext_id in external_ids:
            cfg = (await session.execute(
                select(TaskConfig).where(
                    TaskConfig.external_task_id == ext_id))).scalars().first()
            if cfg is None:
                print(f"skip {ext_id}: config not found")
                continue
            inst = await manual_run_config(session, bot, None, owner, cfg.id)
            await session.commit()
            print(f"run {ext_id}: {'sent' if inst is not None else 'skipped (duplicate)'}")

    await bot.session.close()
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
