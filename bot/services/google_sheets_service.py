"""Заглушка job-обработчика автосинхронизации с Google Sheets.

Реальная реализация — Task 25/26. Сигнатура зафиксирована уже сейчас,
т.к. на неё ссылается SchedulerService.register_sync_job — Task 12.
"""


async def auto_sync_job(bot, session_factory) -> None:
    return None
