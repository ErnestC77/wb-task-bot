from datetime import datetime, timedelta

from bot.database.models import DeliveryStatus, QuestionStatus
from bot.utils.logger import get_logger

logger = get_logger(__name__)
GRACE = 3600


def _not_past(dt: datetime | None) -> datetime:
    now = datetime.utcnow()
    return dt if dt and dt > now else now + timedelta(seconds=5)


def _add_job(scheduler, func, run_date, args, job_id) -> None:
    """add_job(replace_existing=True) обеспечивает идемпотентность только для
    уже запущенного планировщика: APScheduler ставит job'ы в очередь
    ``_pending_jobs`` и разруливает дубли по id лишь в ``scheduler.start()``.
    recover_jobs вызывается ДО start() (см. main.py), поэтому при повторном
    вызове (например, ретрай старта) add_job с тем же id просто копил бы
    дубликаты в очереди. Снимаем уже поставленный job с тем же id вручную —
    remove_job/get_job одинаково работают и для pending, и для запущенного
    планировщика.
    """
    if scheduler.get_job(job_id):
        scheduler.remove_job(job_id)
    scheduler.add_job(func, "date", run_date=run_date, args=args, id=job_id,
                      replace_existing=True, misfire_grace_time=GRACE)


async def recover_jobs(scheduler, bot, session_factory) -> dict[str, int]:
    from bot.database.repositories.question_repository import QuestionRepository
    from bot.database.repositories.task_repository import TaskRepository
    from bot.services.approval_service import auto_approve_job
    from bot.services.delivery_service import DeliveryService
    from bot.services.question_service import escalation_job
    from bot.services.reminder_service import overdue_job, reminder_job
    from bot.services.scheduler_service import SchedulerService, compute_next_run
    from bot.services.setting_service import SettingService

    counters = {"configs": 0, "reminders": 0, "overdue": 0,
                "auto_approve": 0, "questions": 0, "deliveries": 0}
    svc = SchedulerService(scheduler, bot, session_factory)

    async with session_factory() as session:
        repo = TaskRepository(session)
        settings = SettingService(session)

        # 1) конфиги: просроченный next_run_at выполняем немедленно (misfire policy)
        for config in await repo.get_active_configs():
            if config.next_run_at is None:
                config.next_run_at = compute_next_run(config, datetime.utcnow())
            run_at = _not_past(config.next_run_at)
            _add_job(scheduler, svc.run_config, run_at, [config.id], f"config:{config.id}")
            counters["configs"] += 1
        await session.commit()

        # 2) напоминания и overdue открытых задач
        for inst in await repo.get_open_with_reminders():
            base = inst.scheduled_at
            for n, hours in ((1, inst.remind_after_hours_snapshot),
                             (2, inst.second_remind_after_hours_snapshot)):
                if hours:
                    _add_job(scheduler, reminder_job,
                            _not_past(base + timedelta(hours=hours)),
                            [inst.id, n, bot, session_factory], f"remind{n}:{inst.id}")
                    counters["reminders"] += 1
            _add_job(scheduler, overdue_job, _not_past(base + timedelta(hours=24)),
                    [inst.id, bot, session_factory], f"overdue:{inst.id}")
            counters["overdue"] += 1

        # 3) auto-approve для waiting_approval — по snapshot-дедлайну
        for inst in await repo.get_waiting_approval():
            _add_job(scheduler, auto_approve_job, _not_past(inst.approval_deadline_at),
                    [inst.id, bot, session_factory], f"auto_approve:{inst.id}")
            counters["auto_approve"] += 1

        # 4) эскалация неотвеченных вопросов
        esc_hours = int(await settings.get("questions.escalation_hours"))
        questions = QuestionRepository(session)
        for q in await questions.unanswered_older_than(datetime.utcnow()):
            if q.status == QuestionStatus.SENT:
                deadline = (q.created_at or datetime.utcnow()) + timedelta(hours=esc_hours)
                _add_job(scheduler, escalation_job, _not_past(deadline),
                        [q.id, bot, session_factory], f"question_escalation:{q.id}")
                counters["questions"] += 1

        # 5) зависшие доставки pending/failed/retrying.
        # PENDING — процесс упал ДО первой попытки отправки: retry_task_delivery
        # для него no-op (реагирует только на RETRYING), поэтому такие инстансы
        # планируются на deliver_pending, который реально шлёт сообщение.
        delivery = DeliveryService(session, bot, scheduler)
        for inst in await repo.get_pending_delivery():
            if inst.delivery_status == DeliveryStatus.PENDING:
                _add_job(scheduler, delivery.deliver_pending, _not_past(inst.next_retry_at),
                        [inst.id], f"deliver_pending:{inst.id}:recover")
            else:
                _add_job(scheduler, delivery.retry_task_delivery, _not_past(inst.next_retry_at),
                        [inst.id], f"retry_delivery:{inst.id}:recover")
            counters["deliveries"] += 1

    logger.info("Recovery done: %s", counters)
    return counters
