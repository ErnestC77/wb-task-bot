"""Подтверждение задач: запрос approval, кнопки, auto-approve через 24ч (snapshot).

Task 15. auto_approve_job переводит waiting_approval -> auto_approved СТРОГО
через TaskRepository.transition_status с expected_statuses=[waiting_approval]:
если owner/partner успел нажать «Подтвердить»/«Вернуть в работу» чуть раньше
срабатывания job'а, conditional UPDATE не найдёт строку в ожидаемом статусе и
transition_status вернёт None — job тихо завершается без противоречивого
состояния (двойная защита от гонки кнопка vs job).
"""
from datetime import datetime, timedelta

from aiogram import Bot
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models import Role, TaskInstance, TaskStatus, User
from bot.database.repositories.task_repository import TaskRepository
from bot.database.repositories.user_repository import UserRepository
from bot.keyboards.approval_keyboards import approval_keyboard
from bot.services.setting_service import SettingService
from bot.services.task_service import TaskService
from bot.utils.logger import get_logger
from bot.utils.message_templates import render_approval_request

logger = get_logger(__name__)


class ApprovalService:
    def __init__(self, session: AsyncSession, bot: Bot, scheduler=None) -> None:
        self.session = session
        self.bot = bot
        self.scheduler = scheduler
        self.repo = TaskRepository(session)
        self.users = UserRepository(session)
        self.settings = SettingService(session)
        self.tasks = TaskService(session, bot)

    async def can_approve(self, user: User) -> bool:
        mode = str(await self.settings.get("approval.approvers_mode"))
        if mode == "owner":
            return user.role == Role.OWNER
        if mode == "partner":
            return user.role == Role.PARTNER
        return user.role in (Role.OWNER, Role.PARTNER)   # both|first

    async def request_approval(self, inst: TaskInstance, actor: User
                               ) -> TaskInstance | None:
        deadline = datetime.utcnow() + timedelta(
            hours=inst.approval_timeout_hours_snapshot)
        got = await self.tasks.user_transition(
            inst.id, [TaskStatus.IN_PROGRESS], TaskStatus.WAITING_APPROVAL,
            actor, "btn:done", completed_at=datetime.utcnow(),
            approval_deadline_at=deadline)
        if got is None:
            return None
        await self._notify_approvers(got)
        if self.scheduler is not None and bool(
                await self.settings.get("approval.auto_approve_enabled")):
            job_id = f"auto_approve:{got.id}"
            # Явный remove_job перед add_job: replace_existing=True сам по
            # себе идемпотентен только для уже стартовавшего планировщика
            # (см. находки Task 12-13/scheduler_recovery_service._add_job) —
            # до scheduler.start() дубли копятся в _pending_jobs.
            if self.scheduler.get_job(job_id):
                self.scheduler.remove_job(job_id)
            self.scheduler.add_job(
                auto_approve_job, "date", run_date=deadline,
                args=[got.id, self.bot, None],   # session_factory подставляет caller
                id=job_id, replace_existing=True,
                misfire_grace_time=3600)
        return got

    async def _notify_approvers(self, inst: TaskInstance) -> None:
        from bot.database.repositories.topic_repository import TopicRepository
        chat_id = int(await self.settings.get("general.group_chat_id"))
        topic_key = str(await self.settings.get("approval.topic_key"))
        thread_id = None
        topic = await TopicRepository(self.session).get_by_key(topic_key)
        if topic:
            thread_id = topic.message_thread_id
        try:
            await self.bot.send_message(
                chat_id=chat_id, message_thread_id=thread_id,
                text=render_approval_request(inst),
                reply_markup=approval_keyboard(inst.id))
        except Exception as exc:                     # noqa: BLE001
            logger.warning("Approval request send failed: %s", exc)
        if bool(await self.settings.get("approval.send_private")):
            for u in await self.users.get_owners_and_partners():
                try:
                    await self.bot.send_message(
                        chat_id=u.telegram_id, text=render_approval_request(inst),
                        reply_markup=approval_keyboard(inst.id))
                except Exception as exc:             # noqa: BLE001
                    logger.warning("Private approval to %s failed: %s", u.telegram_id, exc)

    async def approve(self, instance_id: int, actor: User) -> TaskInstance | None:
        if not await self.can_approve(actor):
            raise PermissionError("Подтверждать может только owner/partner (по настройке)")
        got = await self.repo.transition_status(
            instance_id, [TaskStatus.WAITING_APPROVAL], TaskStatus.APPROVED,
            actor.id, "btn:approve", approved_at=datetime.utcnow())
        if got is not None:
            self._remove_auto_job(instance_id)
            await self.tasks.refresh_task_message(got)
            if bool(await self.settings.get("approval.notify_on_approve")):
                await self._notify_responsible(got, "✅ Ваша задача подтверждена")
        return got

    async def return_to_work(self, instance_id: int, actor: User,
                             comment: str) -> TaskInstance | None:
        if not await self.can_approve(actor):
            raise PermissionError("Возврат в работу — только для подтверждающих")
        if not bool(await self.settings.get("approval.allow_return_to_work")):
            raise PermissionError("Возврат в работу отключен настройкой")
        if bool(await self.settings.get("approval.return_comment_required")) and not comment:
            raise ValueError("Обязателен комментарий при возврате в работу")
        got = await self.repo.transition_status(
            instance_id, [TaskStatus.WAITING_APPROVAL], TaskStatus.IN_PROGRESS,
            actor.id, "btn:return", comment=comment,
            approval_deadline_at=None, completed_at=None)
        if got is not None:
            self._remove_auto_job(instance_id)
            await self.tasks.refresh_task_message(got)   # рабочие кнопки восстановлены
            await self._notify_responsible(got, f"🔁 Задача возвращена в работу: {comment}")
        return got

    def _remove_auto_job(self, instance_id: int) -> None:
        if self.scheduler is not None and self.scheduler.get_job(f"auto_approve:{instance_id}"):
            self.scheduler.remove_job(f"auto_approve:{instance_id}")

    async def _notify_responsible(self, inst: TaskInstance, text: str) -> None:
        """В чат самой задачи (та же группа/тема, где создана задача), а не
        личным сообщением ответственному — DM недоступен, пока пользователь
        сам не написал боту в личку хотя бы раз, к тому же уведомление в
        общем чате видно всей команде, не только исполнителю."""
        if inst.telegram_chat_id is None:
            return
        try:
            await self.bot.send_message(
                chat_id=inst.telegram_chat_id, message_thread_id=inst.topic_snapshot,
                text=text)
        except Exception as exc:                     # noqa: BLE001
            logger.warning("Notify responsible failed: %s", exc)


async def auto_approve_job(instance_id: int, bot, session_factory) -> None:
    """Auto-approve СТРОГО из waiting_approval (conditional transition)."""
    if session_factory is None:
        from bot.database.db import async_session_factory as session_factory  # noqa: PLW0127
    async with session_factory() as session:
        repo = TaskRepository(session)
        settings = SettingService(session)
        got = await repo.transition_status(
            instance_id, [TaskStatus.WAITING_APPROVAL], TaskStatus.AUTO_APPROVED,
            None, "auto:approve", auto_approved_at=datetime.utcnow())
        if got is None:
            return                                    # уже approved/возвращена — no-op
        await TaskService(session, bot).refresh_task_message(got)
        if bool(await settings.get("approval.notify_on_auto_approve")) \
                and got.telegram_chat_id is not None:
            try:
                await bot.send_message(
                    chat_id=got.telegram_chat_id, message_thread_id=got.topic_snapshot,
                    text="✅ Задача авто-подтверждена (нет реакции)")
            except Exception:                        # noqa: BLE001
                pass
        await session.commit()
