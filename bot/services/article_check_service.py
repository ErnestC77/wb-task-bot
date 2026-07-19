import math
from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models import (
    ArticleAction, ArticleCheckItem, ArticleCheckSession, CheckStatus,
    TaskInstance, TaskStatus, User,
)
from bot.database.repositories.article_check_repository import ArticleCheckRepository
from bot.database.repositories.question_repository import QuestionRepository
from bot.database.repositories.task_repository import TaskRepository
from bot.services.approval_service import ApprovalService
from bot.services.article_service import ArticleService
from bot.services.setting_service import SettingService
from bot.services.task_service import TaskService


@dataclass
class BatchView:
    session: ArticleCheckSession
    items: list[ArticleCheckItem]
    batch: int
    total_batches: int
    checked: int
    total: int


class ArticleCheckService:
    def __init__(self, session: AsyncSession, bot=None,
                approval_service: ApprovalService | None = None) -> None:
        self.session = session
        self.bot = bot
        self.repo = ArticleCheckRepository(session)
        self.tasks = TaskRepository(session)
        self.task_service = TaskService(session, bot)
        self.articles = ArticleService(session)
        self.settings = SettingService(session)
        self.approval_service = approval_service

    async def start_check(self, inst: TaskInstance, actor: User) -> ArticleCheckSession:
        if inst.responsible_user_id != actor.id:
            raise PermissionError("Начать проверку может только ответственный")
        await self.tasks.transition_status(
            inst.id, [TaskStatus.CREATED, TaskStatus.POSTPONED],
            TaskStatus.IN_PROGRESS, actor.id, "btn:start_check")
        existing = await self.repo.get_session_by_instance(inst.id)
        if existing is not None:
            return existing                        # продолжение (в т.ч. после рестарта)
        batch_size = inst.article_batch_size_snapshot or int(
            await self.settings.get("article_check.batch_size"))
        articles = await self.articles.get_active_for_check()
        created = await self.repo.create_session(inst.id, actor.id, batch_size, articles)
        if created is None:                        # гонка повторного старта
            created = await self.repo.get_session_by_instance(inst.id)
        return created

    async def get_batch_view(self, session_id: int, batch: int) -> BatchView:
        s = await self.session.get(ArticleCheckSession, session_id)
        items = await self.repo.get_items_for_batch(session_id, batch, s.batch_size)
        counts = await self.repo.count_by_status(session_id)
        checked = s.total_articles - counts[CheckStatus.PENDING.value]
        total_batches = max(1, math.ceil(s.total_articles / s.batch_size))
        return BatchView(s, items, batch, total_batches, checked, s.total_articles)

    async def mark(self, item_id: int, version: int, status: str, actor: User) -> bool:
        item = await self.repo.get_item(item_id)
        if item is None:
            raise ValueError("Артикул не найден")   # поддельный item ID
        s = await self.session.get(ArticleCheckSession, item.check_session_id)
        if s.responsible_user_id != actor.id:
            raise PermissionError("Отмечать может только ответственный за проверку")
        if item.check_status != CheckStatus.PENDING and not bool(
                await self.settings.get("article_check.allow_change_result")):
            raise PermissionError("Изменение результата запрещено настройкой")
        ok = await self.repo.mark_item(item_id, version, status, actor.id)
        if ok:
            await self._recount(s.id)
        return ok

    async def _recount(self, session_id: int) -> None:
        counts = await self.repo.count_by_status(session_id)
        s = await self.session.get(ArticleCheckSession, session_id)
        s.checked_articles = s.total_articles - counts[CheckStatus.PENDING.value]
        s.action_required_count = counts[CheckStatus.ACTION_REQUIRED.value]
        s.questions_count = counts[CheckStatus.QUESTION.value]
        await self.session.flush()

    async def can_finish_batch(self, session_id: int, batch: int) -> bool:
        if bool(await self.settings.get("article_check.allow_finish_batch_with_pending")):
            return True
        s = await self.session.get(ArticleCheckSession, session_id)
        return await self.repo.pending_in_batch(session_id, batch, s.batch_size) == 0

    async def next_batch(self, session_id: int) -> int:
        s = await self.session.get(ArticleCheckSession, session_id)
        total_batches = max(1, math.ceil(s.total_articles / s.batch_size))
        new_batch = min(s.current_batch + 1, total_batches)
        await self.repo.set_current_batch(session_id, new_batch)
        return new_batch

    async def prev_batch(self, session_id: int) -> int:
        if not bool(await self.settings.get("article_check.allow_prev_batch")):
            raise PermissionError("Переход к предыдущей пачке запрещён настройкой")
        s = await self.session.get(ArticleCheckSession, session_id)
        new_batch = max(1, s.current_batch - 1)
        await self.repo.set_current_batch(session_id, new_batch)
        return new_batch

    async def finish_check(self, inst: TaskInstance, actor: User) -> tuple[bool, str]:
        if inst.responsible_user_id != actor.id:
            raise PermissionError("Завершить проверку может только ответственный")
        s = await self.repo.get_session_by_instance(inst.id)
        if s is None:
            return False, "Проверка не начата"
        counts = await self.repo.count_by_status(s.id)
        if counts[CheckStatus.PENDING.value] > 0:
            return False, f"Осталось непроверенных: {counts[CheckStatus.PENDING.value]}"
        if bool(await self.settings.get("article_check.require_action_for_action_required")):
            missing = await self.repo.items_action_required_without_action(s.id)
            if missing:
                return False, (f"По {len(missing)} артикулам не создано действие "
                               f"(⚠ требуются действия)")
        if not bool(await self.settings.get("article_check.allow_finish_with_open_questions")):
            open_qs = await QuestionRepository(self.session).open_for_instance(inst.id)
            if open_qs:
                return False, f"Есть открытые вопросы: {len(open_qs)}"
        await self.repo.complete_session(s.id)
        if inst.need_approval_snapshot:
            approval = self.approval_service or ApprovalService(self.session, self.bot)
            got = await approval.request_approval(inst, actor)
        else:
            got = await self.tasks.transition_status(
                inst.id, [TaskStatus.IN_PROGRESS], TaskStatus.COMPLETED, actor.id,
                "btn:finish_check", completed_at=datetime.utcnow())
            if got is not None:
                await self.task_service.refresh_task_message(got)
        if got is None:
            return False, "Не удалось завершить проверку: статус задачи изменился"
        return True, "Проверка завершена"

    async def cancel_check(self, inst: TaskInstance, actor: User) -> tuple[bool, str]:
        """«✖ Отменить проверку» в пачке (bot/handlers/article_check.py) — раньше
        кнопка существовала в клавиатуре, но обработчика не было вообще (баг,
        найден при разборе инцидента с article_check config id=1, 2026-07-19):
        нажатие ничего не делало. По образцу finish_check выше."""
        if inst.responsible_user_id != actor.id:
            raise PermissionError("Отменить проверку может только ответственный")
        s = await self.repo.get_session_by_instance(inst.id)
        if s is None:
            return False, "Проверка не начата"
        got = await self.tasks.transition_status(
            inst.id, [TaskStatus.IN_PROGRESS], TaskStatus.CANCELLED, actor.id,
            "btn:cancel_check", cancelled_at=datetime.utcnow())
        if got is None:
            return False, "Не удалось отменить: статус задачи изменился"
        await self.repo.cancel_session(s.id)
        return True, "Проверка отменена"

    async def create_action(self, item_id: int, actor: User, category_id: int,
                            problem_type_id: int, decision_type_id: int,
                            comment: str | None, next_check_date: date) -> ArticleAction:
        from bot.database.models import ArticleCategory, DecisionType, ProblemType
        item = await self.repo.get_item(item_id)
        if item is None:
            raise ValueError("Артикул не найден")
        s = await self.session.get(ArticleCheckSession, item.check_session_id)
        cat = await self.session.get(ArticleCategory, category_id)
        prob = await self.session.get(ProblemType, problem_type_id)
        dec = await self.session.get(DecisionType, decision_type_id)
        for ref, label in ((cat, "категория"), (prob, "проблема"), (dec, "решение")):
            if ref is None or not ref.is_active:
                raise ValueError(f"Недопустимое значение: {label}")
        action = ArticleAction(
            article_check_item_id=item.id, task_instance_id=s.task_instance_id,
            user_id=actor.id, article=item.article_snapshot,
            category_id=cat.id, problem_type_id=prob.id, decision_type_id=dec.id,
            category_name_snapshot=cat.name, problem_name_snapshot=prob.name,
            decision_name_snapshot=dec.name, comment=comment,
            next_check_date=next_check_date)
        self.session.add(action)
        await self.session.flush()
        return action
