"""Разовый бутстрап: добавить/обновить пользователя как owner напрямую в БД.

Используется при первом запуске, когда таблица users ещё пуста и через
/admin зайти некому. Второй способ (см. README) — вручную через psql.

Запуск: python scripts/add_owner.py <telegram_id> <name>
"""
import asyncio
import os
import sys

# python scripts/add_owner.py кладёт на sys.path каталог scripts/, а не
# корень проекта (/app) — bot/ пакет отсюда не виден. Добавляем корень
# явно, чтобы скрипт работал независимо от того, как его запускают
# (PYTHONPATH снаружи может быть не задан, например в one-off job'е PaaS).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from bot.config import get_settings


async def main(telegram_id: int, name: str) -> None:
    engine = create_async_engine(get_settings().database_url)
    async with engine.begin() as conn:
        row = (await conn.execute(
            text(
                "INSERT INTO users (telegram_id, name, role, is_active) "
                "VALUES (:tid, :name, 'owner', true) "
                "ON CONFLICT (telegram_id) DO UPDATE SET role='owner', is_active=true "
                "RETURNING id, telegram_id, name, role, is_active"
            ),
            {"tid": telegram_id, "name": name},
        )).fetchone()
        print("OK:", dict(row._mapping))
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main(int(sys.argv[1]), sys.argv[2]))
