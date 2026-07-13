"""Разовая диагностика: загрузить основные конфигурационные таблицы из JSON
(см. scripts/export_core_data.py) в текущую БД (DATABASE_URL). Схема должна
быть уже создана (alembic upgrade head) — миграция 0002 сама сеет дефолтные
settings/topics/tasks_config, поэтому перед загрузкой эти 4 таблицы
очищаются (TRUNCATE ... CASCADE), иначе INSERT с явным id упадёт на
конфликте с сид-данными. Пишет через RAW INSERT с явным id (чтобы сохранить
внешние ключи между таблицами), затем сбрасывает sequence.

Запуск: python scripts/import_core_data.py /path/to/core_data.json
"""
import asyncio
import json
import os
import sys
from datetime import date, datetime, time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import insert, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.config import get_settings
from bot.database.models import SystemSetting, TaskConfig, Topic, User

TABLES = [
    (User, "users"),
    (Topic, "topics"),
    (TaskConfig, "tasks_config"),
    (SystemSetting, "system_settings"),
]

_PARSERS = {datetime: datetime.fromisoformat, date: date.fromisoformat,
           time: time.fromisoformat}


def _deserialize_rows(model, rows: list[dict]) -> list[dict]:
    py_types = {c.name: c.type.python_type for c in model.__table__.columns
               if c.type.python_type in _PARSERS}
    out = []
    for row in rows:
        fixed = dict(row)
        for col, py_type in py_types.items():
            if fixed.get(col) is not None:
                fixed[col] = _PARSERS[py_type](fixed[col])
        out.append(fixed)
    return out


async def main(path: str) -> None:
    with open(path, encoding="utf-8") as f:
        dump = json.load(f)

    settings = get_settings()
    engine = create_async_engine(settings.database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        table_names = ", ".join(model.__table__.name for model, _ in TABLES)
        await session.execute(text(f"TRUNCATE TABLE {table_names} RESTART IDENTITY CASCADE"))
        for model, name in TABLES:
            rows = dump.get(name, [])
            if not rows:
                continue
            await session.execute(insert(model.__table__), _deserialize_rows(model, rows))
            table = model.__table__.name
            await session.execute(text(
                f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), "
                f"(SELECT COALESCE(MAX(id), 1) FROM {table}))"))
            print(f"{name}: {len(rows)} rows loaded")
        await session.commit()
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1]))
