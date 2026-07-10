"""Заглушка job-обработчика эскалации вопросов.

Реальная реализация — Task 19 (вопросы/эскалации). Сигнатура фиксирована
уже сейчас, т.к. на неё ссылается SchedulerRecoveryService (Task 13).
"""


async def escalation_job(question_id: int, bot, session_factory) -> None:
    return None
