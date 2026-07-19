from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models import (
    Article, ArticleAction, ArticleCheckItem, ArticleCheckSession,
    CheckStatus, SessionStatus,
)


class ArticleCheckRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_session(self, task_instance_id: int, responsible_user_id: int,
                             batch_size: int, articles: list[Article],
                             ) -> ArticleCheckSession | None:
        # Идемпотентность (защита от повторного старта) держится на
        # UniqueConstraint(task_instance_id) у ArticleCheckSession в models.py.
        chk = ArticleCheckSession(
            task_instance_id=task_instance_id, responsible_user_id=responsible_user_id,
            status=SessionStatus.ACTIVE, total_articles=len(articles),
            batch_size=batch_size, current_batch=1, started_at=datetime.utcnow())
        try:
            async with self.session.begin_nested():
                self.session.add(chk)
                await self.session.flush()
                self.session.add_all([
                    ArticleCheckItem(check_session_id=chk.id, article_id=a.id,
                                     article_snapshot=a.article,
                                     product_name_snapshot=a.product_name,
                                     sort_order=i)
                    for i, a in enumerate(articles)])
                await self.session.flush()
        except IntegrityError:
            return None            # повторный старт — сессия уже существует
        return chk

    async def get_session_by_instance(self, task_instance_id: int
                                      ) -> ArticleCheckSession | None:
        return await self.session.scalar(select(ArticleCheckSession).where(
            ArticleCheckSession.task_instance_id == task_instance_id))

    async def get_items_for_batch(self, session_id: int, batch: int,
                                  batch_size: int) -> list[ArticleCheckItem]:
        return list(await self.session.scalars(
            select(ArticleCheckItem)
            .where(ArticleCheckItem.check_session_id == session_id)
            .order_by(ArticleCheckItem.sort_order)
            .offset((batch - 1) * batch_size).limit(batch_size)))

    async def get_item(self, item_id: int) -> ArticleCheckItem | None:
        return await self.session.get(ArticleCheckItem, item_id)

    async def mark_item(self, item_id: int, expected_version: int,
                        status: str, user_id: int) -> bool:
        """Optimistic locking: UPDATE ... WHERE id AND version."""
        result = await self.session.execute(
            update(ArticleCheckItem)
            .where(ArticleCheckItem.id == item_id,
                   ArticleCheckItem.version == expected_version)
            .values(check_status=status, checked_by_user_id=user_id,
                    checked_at=datetime.utcnow(), version=expected_version + 1))
        await self.session.flush()
        return result.rowcount == 1

    async def count_by_status(self, session_id: int) -> dict[str, int]:
        rows = await self.session.execute(
            select(ArticleCheckItem.check_status, func.count())
            .where(ArticleCheckItem.check_session_id == session_id)
            .group_by(ArticleCheckItem.check_status))
        counts = {s.value: 0 for s in CheckStatus}
        counts.update({status: n for status, n in rows})
        return counts

    async def pending_in_batch(self, session_id: int, batch: int, batch_size: int) -> int:
        items = await self.get_items_for_batch(session_id, batch, batch_size)
        return sum(1 for i in items if i.check_status == CheckStatus.PENDING)

    async def set_current_batch(self, session_id: int, batch: int) -> None:
        await self.session.execute(update(ArticleCheckSession)
                                   .where(ArticleCheckSession.id == session_id)
                                   .values(current_batch=batch))
        await self.session.flush()

    async def complete_session(self, session_id: int) -> None:
        await self.session.execute(update(ArticleCheckSession)
                                   .where(ArticleCheckSession.id == session_id)
                                   .values(status=SessionStatus.COMPLETED,
                                           completed_at=datetime.utcnow()))
        await self.session.flush()

    async def cancel_session(self, session_id: int) -> None:
        await self.session.execute(update(ArticleCheckSession)
                                   .where(ArticleCheckSession.id == session_id)
                                   .values(status=SessionStatus.CANCELLED))
        await self.session.flush()

    async def items_action_required_without_action(self, session_id: int
                                                   ) -> list[ArticleCheckItem]:
        sub = select(ArticleAction.article_check_item_id).where(
            ArticleAction.article_check_item_id.is_not(None))
        return list(await self.session.scalars(
            select(ArticleCheckItem).where(
                ArticleCheckItem.check_session_id == session_id,
                ArticleCheckItem.check_status == CheckStatus.ACTION_REQUIRED,
                ArticleCheckItem.id.not_in(sub))))
