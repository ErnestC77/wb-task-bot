"""Заглушка job-обработчика еженедельного отчёта.

Реальная реализация — Task 21. Сигнатура зафиксирована уже сейчас, т.к.
на неё ссылается SchedulerService.register_report_job — Task 12.
"""


async def weekly_report_job(bot, session_factory) -> None:
    return None
