"""Разовая диагностика: выгрузить основные конфигурационные таблицы (без
тестового мусора — истории задач/аудита/вопросов) в JSON на stdout, для
переноса на новый сервер. Читает по внутреннему DATABASE_URL (текущего
хоста), пишет один JSON-объект в лог job'а.

Запуск: python scripts/export_core_data.py
"""
import asyncio
import json
import os
import sys
from datetime import date, datetime, time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.config import get_settings
from bot.database.models import SystemSetting, TaskConfig, Topic, User

TABLES = [
    (User, "users"),
    (Topic, "topics"),
    (TaskConfig, "tasks_config"),
    (SystemSetting, "system_settings"),
]


def _serialize(value):
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    return value


async def main() -> None:
    settings = get_settings()
    engine = create_async_engine(settings.database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    dump = {}
    async with session_factory() as session:
        for model, name in TABLES:
            rows = (await session.execute(select(model))).scalars().all()
            cols = model.__table__.columns.keys()
            dump[name] = [
                {c: _serialize(getattr(r, c)) for c in cols} for r in rows]
    print("===DUMP_START===")
    print(json.dumps(dump, ensure_ascii=False))
    print("===DUMP_END===")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
