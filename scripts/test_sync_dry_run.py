"""Разовая диагностика: реальный dry-run синхронизации Google Sheets против
боевой БД. sync_all(dry_run=True) откатывает каждый SAVEPOINT — в бизнес-
таблицы ничего не пишется, только читает реальный Google Sheet и печатает
итоговый SyncReport по каждому листу.

Запуск: python scripts/test_sync_dry_run.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.config import get_settings
from bot.database.models import User
from bot.services.google_sheets_service import GoogleSheetsService, SheetsClient
from bot.services.setting_service import SettingService


async def main() -> None:
    settings = get_settings()
    engine = create_async_engine(settings.database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        owner = (await session.execute(
            select(User).where(User.telegram_id == 910256253))).scalars().first()
        settings_svc = SettingService(session)
        spreadsheet_id = (str(await settings_svc.get("sync.spreadsheet_id"))
                           or settings.google_sheets_spreadsheet_id)
        client = SheetsClient(settings.google_sheets_credentials_file, spreadsheet_id)
        svc = GoogleSheetsService(session, client=client)
        report = await svc.sync_all(
            dry_run=True, actor_user_id=owner.id if owner else None)
        await session.commit()
        for kind, r in report.items():
            print(f"{kind}: added={r.added} updated={r.updated} "
                  f"deactivated={r.deactivated} skipped_conflicts={r.skipped_conflicts}")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
