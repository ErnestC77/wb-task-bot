"""Разовая диагностика: пересчитать next_run_at для ВСЕХ активных шаблонов
задач относительно текущего момента (utcnow) — на случай, если сохранённые
значения устарели (например, после ручных правок конфигов в обход
scheduler.rebuild_config_job). После запуска нужен рестарт бота, чтобы
recover_jobs() перечитал свежие next_run_at из БД.

Запуск: python scripts/rebuild_all_schedules.py
"""
import asyncio
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.config import get_settings
from bot.database.models import TaskConfig
from bot.services.scheduler_service import compute_next_run


async def main() -> None:
    settings = get_settings()
    engine = create_async_engine(settings.database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.utcnow()
    async with session_factory() as session:
        configs = (await session.execute(
            select(TaskConfig).where(TaskConfig.is_active == True))).scalars().all()  # noqa: E712
        for cfg in configs:
            old = cfg.next_run_at
            cfg.next_run_at = compute_next_run(cfg, now, after_change=True)
            print(f"{cfg.external_task_id}: {old} -> {cfg.next_run_at}")
        await session.commit()
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
