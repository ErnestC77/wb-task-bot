from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models import ArticleAction, TaskInstance, TaskLog, TaskQuestion


class ReportRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_instances_for_period(self, start: datetime, end: datetime) -> list[TaskInstance]:
        return list(await self.session.scalars(
            select(TaskInstance).where(
                TaskInstance.scheduled_at >= start,
                TaskInstance.scheduled_at < end)
            .order_by(TaskInstance.scheduled_at)))

    async def get_actions_for_period(self, start: datetime, end: datetime) -> list[TaskLog]:
        return list(await self.session.scalars(
            select(TaskLog).where(
                TaskLog.created_at >= start,
                TaskLog.created_at < end)
            .order_by(TaskLog.created_at)))

    async def get_open_questions(self) -> list[TaskQuestion]:
        return list(await self.session.scalars(
            select(TaskQuestion).where(
                TaskQuestion.status.not_in(["answered", "closed"]))))

    async def get_expired_actions(self, today: date) -> list[ArticleAction]:
        return list(await self.session.scalars(
            select(ArticleAction).where(
                ArticleAction.status == "open",
                ArticleAction.next_check_date < today)))
