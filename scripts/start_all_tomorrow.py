"""Разовая диагностика: выставить next_run_at = завтра 09:00 (время создания
задачи, config.time) для ВСЕХ активных шаблонов — единый старт с
завтрашнего дня, дальше каждый идёт по своему обычному циклу
(every_n_days/weekly/...) от этой точки. После запуска нужен рестарт бота.

Запуск: python scripts/start_all_tomorrow.py
"""
import asyncio
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.config import get_settings
from bot.database.models import TaskConfig


async def main() -> None:
    settings = get_settings()
    engine = create_async_engine(settings.database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    tomorrow = (datetime.utcnow() + timedelta(days=1)).date()
    async with session_factory() as session:
        configs = (await session.execute(
            select(TaskConfig).where(TaskConfig.is_active == True))).scalars().all()  # noqa: E712
        for cfg in configs:
            fire_time = cfg.time or datetime.min.time().replace(hour=9)
            old = cfg.next_run_at
            cfg.next_run_at = datetime.combine(tomorrow, fire_time)
            print(f"{cfg.external_task_id}: {old} -> {cfg.next_run_at}")
        await session.commit()
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
