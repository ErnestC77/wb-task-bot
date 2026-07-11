import asyncio

from bot.config import get_settings
from bot.database.db import init_db
from bot.loader import create_bot, create_dispatcher
from bot.utils.logger import get_logger, setup_logging

logger = get_logger(__name__)


async def main() -> None:
    settings = get_settings()
    setup_logging(settings.log_level)
    init_db(settings.database_url)

    bot = create_bot(settings.bot_token)
    dp = create_dispatcher()

    from bot.database.db import async_session_factory
    from bot.services.scheduler_recovery_service import recover_jobs
    from bot.services.scheduler_service import setup_scheduler

    scheduler = await setup_scheduler(bot, async_session_factory)
    await recover_jobs(scheduler, bot, async_session_factory)
    # доступ из handlers через dispatcher["scheduler"].wb_service (Task 24:
    # редактирование настроек, влияющих на планировщик, пересобирает jobs).
    dp["scheduler"] = scheduler
    scheduler.start()
    logger.info("Scheduler started (jobs recovered)")

    try:
        logger.info("Starting polling")
        await dp.start_polling(bot)
    finally:
        scheduler.shutdown(wait=False)
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
