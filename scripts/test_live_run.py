"""Разовая диагностика: применить реальную синхронизацию, назначить владельца
ответственным по всем шаблонам задач (для живого теста) и запустить каждый
шаблон немедленно («▶ Запустить сейчас»), чтобы сообщения реально пришли в
группу.

Запуск: python scripts/test_live_run.py
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
from bot.services.google_sheets_service import GoogleSheetsService, SheetsClient
from bot.services.setting_service import SettingService


async def main() -> None:
    settings = get_settings()
    engine = create_async_engine(settings.database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    bot = create_bot(settings.bot_token)  # тот же parse_mode=HTML, что у боевого бота

    async with session_factory() as session:
        owner = (await session.execute(
            select(User).where(User.telegram_id == 910256253))).scalars().first()

        settings_svc = SettingService(session)
        spreadsheet_id = (str(await settings_svc.get("sync.spreadsheet_id"))
                           or settings.google_sheets_spreadsheet_id)
        client = SheetsClient(settings.google_sheets_credentials_file, spreadsheet_id)
        report = await GoogleSheetsService(session, client).sync_all(
            dry_run=False, actor_user_id=owner.id)
        await session.commit()
        for kind, r in report.items():
            print(f"sync {kind}: added={r.added} updated={r.updated} "
                  f"deactivated={r.deactivated}")

        configs = (await session.execute(
            select(TaskConfig).where(TaskConfig.is_active == True))).scalars().all()  # noqa: E712
        for cfg in configs:
            cfg.responsible_user_id = owner.id
        await session.commit()
        print(f"responsible_user_id -> owner for {len(configs)} configs")

        for cfg in configs:
            inst = await manual_run_config(session, bot, None, owner, cfg.id)
            await session.commit()
            print(f"run {cfg.external_task_id}: "
                  f"{'sent' if inst is not None else 'skipped (duplicate)'}")

    await bot.session.close()
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
