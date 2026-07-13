"""Разовая диагностика: применить реальную синхронизацию Google Sheets, без
повторной рассылки задач (в отличие от test_live_run.py).

Запуск: python scripts/apply_sync_only.py
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
        report = await GoogleSheetsService(session, client).sync_all(
            dry_run=False, actor_user_id=owner.id if owner else None)
        await session.commit()
        for kind, r in report.items():
            print(f"sync {kind}: added={r.added} updated={r.updated} "
                  f"deactivated={r.deactivated} skipped_conflicts={r.skipped_conflicts}")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
