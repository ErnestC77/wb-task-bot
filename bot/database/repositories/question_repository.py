from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models import TaskQuestion


class QuestionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, **fields) -> TaskQuestion:
        question = TaskQuestion(**fields)
        self.session.add(question)
        await self.session.flush()
        return question

    async def get(self, question_id: int) -> TaskQuestion | None:
        return await self.session.get(TaskQuestion, question_id)

    async def open_for_instance(self, task_instance_id: int) -> list[TaskQuestion]:
        return list(await self.session.scalars(
            select(TaskQuestion).where(
                TaskQuestion.task_instance_id == task_instance_id,
                TaskQuestion.status.not_in(["answered", "closed"]))))

    async def unanswered_older_than(self, dt: datetime) -> list[TaskQuestion]:
        return list(await self.session.scalars(
            select(TaskQuestion).where(
                TaskQuestion.status.not_in(["answered", "closed"]),
                TaskQuestion.created_at < dt)))

    async def set_status(self, question_id: int, status: str, **timestamps) -> None:
        question = await self.get(question_id)
        if question is not None:
            question.status = status
            for key, value in timestamps.items():
                setattr(question, key, value)
            await self.session.flush()
