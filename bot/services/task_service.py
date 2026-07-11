from datetime import datetime, time, timedelta

from aiogram import Bot
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models import TaskConfig, TaskInstance, TaskScenario, TaskStatus, User
from bot.database.repositories.task_repository import TaskRepository
from bot.database.repositories.topic_repository import TopicRepository
from bot.services.setting_service import SettingService
from bot.utils.datetime_utils import now_tz
from bot.utils.logger import get_logger

logger = get_logger(__name__)


class TaskService:
    def __init__(self, session: AsyncSession, bot: Bot | None = None) -> None:
        self.session = session
        self.bot = bot
        self.repo = TaskRepository(session)
        self.topics = TopicRepository(session)
        self.settings = SettingService(session)

    async def build_snapshot(self, config: TaskConfig) -> dict:
        thread_id = None
        if config.topic_id:
            topic = await self.topics.get_by_id(config.topic_id)
            thread_id = topic.message_thread_id if topic else None
        is_check = config.scenario == TaskScenario.ARTICLE_CHECK
        receiver = config.question_receiver_user_id
        if receiver is None:
            default_receiver = int(await self.settings.get("questions.default_receiver_user_id"))
            receiver = default_receiver or None
        return dict(
            title_snapshot=config.title,
            description_snapshot=config.description,
            responsible_name_snapshot=(config.responsible_user.name
                                       if config.responsible_user else None),
            need_approval_snapshot=config.need_approval,
            approval_timeout_hours_snapshot=int(
                await self.settings.get("approval.timeout_hours")),
            remind_after_hours_snapshot=config.remind_after_hours or int(
                await self.settings.get("reminders.first_after_hours")),
            second_remind_after_hours_snapshot=config.second_remind_after_hours or int(
                await self.settings.get("reminders.second_after_hours")),
            question_receiver_snapshot=receiver,
            topic_snapshot=thread_id,
            article_batch_size_snapshot=(int(
                await self.settings.get("article_check.batch_size")) if is_check else None),
            scenario_snapshot=config.scenario,
            settings_version=await self.settings.current_version(),
        )

    async def create_instance_for(self, config: TaskConfig,
                                  scheduled_at: datetime) -> TaskInstance | None:
        due_at = (datetime.combine(scheduled_at.date(), config.due_time)
                  if config.due_time else scheduled_at + timedelta(hours=24))
        snapshot = await self.build_snapshot(config)
        inst = await self.repo.create_instance_idempotent(
            config, scheduled_at, due_at, snapshot)
        if inst is None:
            logger.info("Instance for config=%s at %s already exists", config.id, scheduled_at)
        return inst

    def ensure_responsible(self, instance: TaskInstance, actor: User) -> None:
        if instance.responsible_user_id != actor.id:
            raise PermissionError("Менять статус может только ответственный сотрудник")

    async def user_transition(self, instance_id: int, expected: list[str],
                              new_status: str, actor: User, action: str,
                              comment: str | None = None, **ts) -> TaskInstance | None:
        inst = await self.repo.get_instance(instance_id)
        if inst is None:
            return None
        self.ensure_responsible(inst, actor)
        got = await self.repo.transition_status(instance_id, expected, new_status,
                                                actor.id, action, comment, **ts)
        if got is not None:
            await self.refresh_task_message(got)
        return got

    async def system_transition(self, instance_id: int, expected: list[str],
                                new_status: str, action: str,
                                comment: str | None = None, **ts) -> TaskInstance | None:
        got = await self.repo.transition_status(instance_id, expected, new_status,
                                                None, action, comment, **ts)
        if got is not None:
            await self.refresh_task_message(got)
        return got

    async def postpone_to_tomorrow(self, instance_id: int, actor: User) -> TaskInstance | None:
        inst = await self.repo.get_instance(instance_id)
        if inst is None:
            return None
        # "Завтра" считаем от реального текущего дня (general.timezone), а
        # не от inst.scheduled_date: перенос может случиться через несколько
        # суток после того, как задача была запланирована (например, после
        # нескольких циклов reminder/overdue), и scheduled_date + 1 день
        # тогда оказывается датой в прошлом.
        tz_name = str(await self.settings.get("general.timezone"))
        today_local = now_tz(tz_name).date()
        tomorrow = datetime.combine(today_local + timedelta(days=1), time(9, 0))
        return await self.user_transition(
            instance_id, [TaskStatus.CREATED, TaskStatus.IN_PROGRESS],
            TaskStatus.POSTPONED, actor, "btn:postpone",
            postponed_to=tomorrow, due_at=tomorrow + timedelta(hours=3))

    async def refresh_task_message(self, inst: TaskInstance) -> None:
        """10.6: обновить исходное сообщение задачи (текст статуса + кнопки)."""
        if self.bot is None or not inst.telegram_message_id:
            return
        from bot.keyboards.task_keyboards import keyboard_for_status
        from bot.utils.message_templates import render_task_message
        try:
            await self.bot.edit_message_text(
                chat_id=inst.telegram_chat_id, message_id=inst.telegram_message_id,
                text=render_task_message(inst),
                reply_markup=keyboard_for_status(inst))
        except Exception as exc:                     # noqa: BLE001 — не рушим переход
            logger.warning("edit_message failed for instance=%s: %s", inst.id, exc)
