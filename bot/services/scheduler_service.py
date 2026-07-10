from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler


async def setup_scheduler(bot: Bot, session_factory) -> AsyncIOScheduler:
    return AsyncIOScheduler()
