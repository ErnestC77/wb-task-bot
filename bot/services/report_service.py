"""Недельный отчёт (Task 21): метрики за период + рендер текста + рассылка.

Все динамические значения (имена сотрудников, названия проблем/решений,
комментарии, номера артикулов) обязательно проходят через html_escape —
см. Global Constraint в bot/utils/message_templates.py.
"""
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from aiogram import Bot
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models import ArticleAction, TaskQuestion, TaskStatus
from bot.database.repositories.report_repository import ReportRepository
from bot.services.setting_service import SettingService
from bot.utils.html_utils import bold, html_escape
from bot.utils.logger import get_logger

logger = get_logger(__name__)

COMPLETED_STATUSES = frozenset(
    {TaskStatus.APPROVED, TaskStatus.AUTO_APPROVED, TaskStatus.COMPLETED})


@dataclass
class ReportData:
    total: int
    completed: int
    overdue: int
    postponed: int
    in_progress: int
    completion_pct: int
    per_employee: dict[str, dict[str, int]] = field(default_factory=dict)
    auto_approved: int = 0
    problem_articles: list[ArticleAction] = field(default_factory=list)
    open_questions: list[TaskQuestion] = field(default_factory=list)
    expired_checks: list[ArticleAction] = field(default_factory=list)


class ReportService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = ReportRepository(session)
        self.settings = SettingService(session)

    async def collect_metrics(self, start: datetime, end: datetime,
                              employee_id: int | None = None) -> ReportData:
        """Метрики за [start, end). employee_id — сузить отчёт до одного
        сотрудника (используется /report для не-owner/partner — «свой отчёт»);
        None (по умолчанию) — полный отчёт по всем."""
        instances = await self.repo.get_instances_for_period(start, end)
        if employee_id is not None:
            instances = [i for i in instances if i.responsible_user_id == employee_id]

        total = len(instances)
        completed = sum(1 for i in instances if i.status in COMPLETED_STATUSES)
        overdue = sum(1 for i in instances if i.status == TaskStatus.OVERDUE)
        postponed = sum(1 for i in instances if i.status == TaskStatus.POSTPONED)
        in_progress = sum(1 for i in instances if i.status == TaskStatus.IN_PROGRESS)
        auto_approved = sum(1 for i in instances if i.status == TaskStatus.AUTO_APPROVED)
        completion_pct = round(completed / total * 100) if total else 0

        per_employee: dict[str, dict[str, int]] = {}
        for inst in instances:
            name = inst.responsible_name_snapshot or "Без ответственного"
            stat = per_employee.setdefault(
                name, {"total": 0, "completed": 0, "overdue": 0, "postponed": 0})
            stat["total"] += 1
            if inst.status in COMPLETED_STATUSES:
                stat["completed"] += 1
            if inst.status == TaskStatus.OVERDUE:
                stat["overdue"] += 1
            if inst.status == TaskStatus.POSTPONED:
                stat["postponed"] += 1

        problem_articles = await self.repo.get_actions_for_period(start, end)
        open_questions = await self.repo.get_open_questions()
        expired_checks = await self.repo.get_expired_actions(date.today())
        if employee_id is not None:
            problem_articles = [a for a in problem_articles if a.user_id == employee_id]
            open_questions = [q for q in open_questions if q.to_user_id == employee_id]
            expired_checks = [a for a in expired_checks if a.user_id == employee_id]

        return ReportData(
            total=total, completed=completed, overdue=overdue, postponed=postponed,
            in_progress=in_progress, completion_pct=completion_pct,
            per_employee=per_employee, auto_approved=auto_approved,
            problem_articles=problem_articles, open_questions=open_questions,
            expired_checks=expired_checks)

    async def render(self, data: ReportData, fmt: str) -> str:
        """fmt: "short" — только сводные счётчики; "full" — плюс детальные
        секции. Каждая секция (кроме итоговой сводки) управляется своим
        флагом reports.show_* — ничего не показывается «всегда»."""
        lines = [bold("📊 Еженедельный отчёт"), ""]
        lines.append(f"Всего задач: {data.total}")
        lines.append(f"Выполнено: {data.completed} ({data.completion_pct}%)")
        if await self.settings.get("reports.show_overdue"):
            lines.append(f"Просрочено: {data.overdue}")
        lines.append(f"Перенесено: {data.postponed}")
        lines.append(f"В работе: {data.in_progress}")
        if await self.settings.get("reports.show_auto_approved"):
            lines.append(f"Авто-подтверждено: {data.auto_approved}")

        if fmt != "full":
            return "\n".join(lines)

        if await self.settings.get("reports.show_per_employee") and data.per_employee:
            lines += ["", bold("По сотрудникам:")]
            for name, stat in data.per_employee.items():
                extra = []
                if stat["overdue"]:
                    extra.append(f"просрочено {stat['overdue']}")
                if stat["postponed"]:
                    extra.append(f"перенесено {stat['postponed']}")
                suffix = f" ({', '.join(extra)})" if extra else ""
                lines.append(
                    f"— {html_escape(name)}: {stat['completed']}/{stat['total']}{suffix}")

        if await self.settings.get("reports.show_problem_articles") and data.problem_articles:
            lines += ["", bold("Проблемные артикулы:")]
            for a in data.problem_articles:
                decision = (f" → {html_escape(a.decision_name_snapshot)}"
                            if a.decision_name_snapshot else "")
                comment = f" ({html_escape(a.comment)})" if a.comment else ""
                lines.append(f"— {html_escape(a.article)}: "
                             f"{html_escape(a.problem_name_snapshot)}{decision}{comment}")

        if await self.settings.get("reports.show_open_questions") and data.open_questions:
            lines += ["", bold(f"Открытые вопросы: {len(data.open_questions)}")]

        if await self.settings.get("reports.show_expired_checks") and data.expired_checks:
            lines += ["", bold("Просроченные проверки:")]
            for a in data.expired_checks:
                problem = f": {html_escape(a.problem_name_snapshot)}" if a.problem_name_snapshot else ""
                lines.append(
                    f"— {html_escape(a.article)}{problem} "
                    f"(срок проверки: {html_escape(a.next_check_date)})")

        return "\n".join(lines)

    async def build_weekly_report(self) -> str:
        period_days = int(await self.settings.get("reports.period_days"))
        fmt = str(await self.settings.get("reports.format"))
        end = datetime.utcnow()
        start = end - timedelta(days=period_days)
        data = await self.collect_metrics(start, end)
        return await self.render(data, fmt)


async def weekly_report_job(bot: Bot, session_factory,
                            *, session: AsyncSession | None = None) -> None:
    """Job-обработчик (регистрируется SchedulerService.register_report_job).

    Формирует отчёт и рассылает его в тему reports.topic_key и лично каждому
    id из reports.private_receiver_ids. Ошибки отправки логируются и не
    прерывают рассылку остальным получателям (тот же паттерн, что и в
    reminder_service/approval_service).

    `session` — опционально уже открытая сессия (Task 32: вызов из
    админ-панели по кнопке «Сформировать и отправить сейчас», где сессия уже
    открыта DbSessionMiddleware на текущий update). Если не передана (обычный
    путь — вызов планировщиком), открывается новая через `session_factory`.
    """
    if session is not None:
        await _send_weekly_report(bot, session)
        return
    async with session_factory() as session:
        await _send_weekly_report(bot, session)


async def _send_weekly_report(bot: Bot, session: AsyncSession) -> None:
    from bot.database.repositories.topic_repository import TopicRepository

    svc = ReportService(session)
    settings = SettingService(session)
    text = await svc.build_weekly_report()

    if bool(await settings.get("reports.send_to_group")):
        chat_id = int(await settings.get("general.group_chat_id"))
        topic_key = str(await settings.get("reports.topic_key"))
        topic = await TopicRepository(session).get_by_key(topic_key)
        thread_id = topic.message_thread_id if topic else None
        try:
            await bot.send_message(chat_id=chat_id, message_thread_id=thread_id, text=text)
        except Exception as exc:                 # noqa: BLE001 — не рушим job
            logger.warning("Weekly report topic send failed: %s", exc)

    owner_id = int(await settings.get("reports.owner_receiver_id"))
    if owner_id:
        from bot.database.repositories.user_repository import UserRepository
        owner = await UserRepository(session).get_by_id(owner_id)
        if owner is not None:
            try:
                await bot.send_message(chat_id=owner.telegram_id, text=text)
            except Exception as exc:             # noqa: BLE001 — не рушим job
                logger.warning("Weekly report owner send to %s failed: %s",
                               owner.telegram_id, exc)

    for receiver_id in list(await settings.get("reports.private_receiver_ids")):
        try:
            # private_receiver_ids хранит сырые Telegram chat_id, не users.id —
            # получатели могут быть не зарегистрированы в боте, поэтому здесь
            # нет резолва через UserRepository (осознанно, подтверждено
            # владельцем продукта).
            await bot.send_message(chat_id=int(receiver_id), text=text)
        except Exception as exc:                 # noqa: BLE001 — не рушим job
            logger.warning("Weekly report private send to %s failed: %s",
                           receiver_id, exc)
