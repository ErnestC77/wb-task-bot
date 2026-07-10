"""Заглушки job-обработчиков напоминаний/просрочки.

Реальная реализация — Task 14 (напоминания) и Task 22 (overdue/эскалации).
Сигнатуры фиксированы уже сейчас, т.к. на них ссылается SchedulerService
(register_instance_jobs) — Task 12.
"""


async def reminder_job(instance_id: int, level: int, bot, session_factory) -> None:
    return None


async def overdue_job(instance_id: int, bot, session_factory) -> None:
    return None
