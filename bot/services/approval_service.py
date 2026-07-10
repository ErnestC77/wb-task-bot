"""Заглушка job-обработчика авто-подтверждения задач.

Реальная реализация — Task 15 (approval flow). Сигнатура фиксирована уже
сейчас, т.к. на неё ссылается SchedulerRecoveryService (Task 13).
"""


async def auto_approve_job(instance_id: int, bot, session_factory) -> None:
    return None
