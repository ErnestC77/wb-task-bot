from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram import BaseMiddleware


def create_bot(token: str) -> Bot:
    return Bot(token=token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))


class DbSessionMiddleware(BaseMiddleware):
    def __init__(self, session_factory):
        self.session_factory = session_factory

    async def __call__(self, handler, event, data):
        async with self.session_factory() as session:
            data["session"] = session
            result = await handler(event, data)
            await session.commit()
            return result


def create_dispatcher() -> Dispatcher:
    dp = Dispatcher(storage=MemoryStorage())
    from bot.database.db import async_session_factory
    from bot.handlers import article_check, callbacks, questions, reports, start, tasks
    from bot.handlers.admin import admin_router

    dp.include_router(start.router)
    dp.include_router(admin_router)
    dp.include_router(tasks.router)
    dp.include_router(reports.router)
    dp.include_router(article_check.router)   # FSM-роутеры раньше callbacks
    dp.include_router(questions.router)
    dp.include_router(callbacks.router)
    dp.update.middleware(DbSessionMiddleware(async_session_factory))
    return dp
