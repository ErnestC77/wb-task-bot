from datetime import datetime, timedelta

from aiogram import Bot
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models import (
    ArticleAction, ArticleCheckItem, DeliveryStatus, QuestionStatus, TaskInstance,
    TaskQuestion, User,
)
from bot.database.repositories.question_repository import QuestionRepository
from bot.database.repositories.user_repository import UserRepository
from bot.keyboards.question_keyboards import answer_keyboard
from bot.services.setting_service import SettingService
from bot.utils.html_utils import html_escape
from bot.utils.logger import get_logger
from bot.utils.message_templates import render_question_message

logger = get_logger(__name__)


class QuestionService:
    def __init__(self, session: AsyncSession, bot: Bot, scheduler=None) -> None:
        self.session = session
        self.bot = bot
        self.scheduler = scheduler
        self.repo = QuestionRepository(session)
        self.users = UserRepository(session)
        self.settings = SettingService(session)

    async def _latest_category_name(self, item: ArticleCheckItem) -> str | None:
        """Категория артикула не хранится на самом ArticleCheckItem (её ещё может
        не быть на момент вопроса) — берём последнюю известную категорию из
        истории ArticleAction по этому артикулу, если она когда-либо фиксировалась."""
        return await self.session.scalar(
            select(ArticleAction.category_name_snapshot)
            .where(ArticleAction.article == item.article_snapshot,
                   ArticleAction.category_name_snapshot.is_not(None))
            .order_by(ArticleAction.id.desc()).limit(1))

    async def resolve_receiver(self, inst: TaskInstance,
                               item: ArticleCheckItem | None = None) -> User:
        candidates: list[int] = []
        if item is not None:
            route = dict(await self.settings.get("questions.route_by_category"))
            if route:
                # маршрутизация по категории товара — только если у ЭТОГО артикула
                # реально известна категория (из истории ArticleAction) и она есть
                # в маппинге; иначе просто идём дальше по приоритету, а не берём
                # случайного получателя из route.
                category_name = await self._latest_category_name(item)
                if category_name and category_name in route:
                    candidates.append(int(route[category_name]))
        if inst.question_receiver_snapshot:
            candidates.append(inst.question_receiver_snapshot)
        candidates.append(int(await self.settings.get("questions.default_receiver_user_id")))
        candidates.append(int(await self.settings.get("questions.fallback_receiver_user_id")))
        for user_id in candidates:
            if not user_id:
                continue
            user = await self.users.get_by_id(user_id)
            if user is not None and user.is_active:
                return user
        raise ValueError("Не настроен активный получатель вопросов")

    async def ask(self, inst: TaskInstance, from_user: User, text: str,
                  item: ArticleCheckItem | None = None) -> TaskQuestion:
        receiver = await self.resolve_receiver(inst, item)
        q = await self.repo.create(
            task_instance_id=inst.id,
            article_check_item_id=item.id if item is not None else None,
            from_user_id=from_user.id, to_user_id=receiver.id,
            question_text=text, status=QuestionStatus.CREATED)
        message = render_question_message(q, inst, item)
        try:
            msg = await self.bot.send_message(chat_id=receiver.telegram_id, text=message,
                                              reply_markup=answer_keyboard(q.id))
            q.status = QuestionStatus.SENT
            q.delivery_status = DeliveryStatus.SENT
            q.telegram_chat_id, q.telegram_message_id = msg.chat.id, msg.message_id
        except Exception as exc:                     # noqa: BLE001
            q.status = QuestionStatus.DELIVERY_FAILED
            q.delivery_status = DeliveryStatus.FAILED
            q.delivery_attempts += 1
            q.last_delivery_error = str(exc)
            q.next_retry_at = datetime.utcnow() + timedelta(minutes=5)
            logger.warning("Question delivery failed: %s", exc)
        if self.scheduler is not None and q.status == QuestionStatus.SENT:
            hours = int(await self.settings.get("questions.escalation_hours"))
            self.scheduler.add_job(
                escalation_job, "date",
                run_date=datetime.utcnow() + timedelta(hours=hours),
                args=[q.id, self.bot, None], id=f"question_escalation:{q.id}",
                replace_existing=True, misfire_grace_time=3600)
        await self.session.flush()
        return q

    async def answer(self, question_id: int, answering_user: User,
                     text: str) -> TaskQuestion:
        q = await self.repo.get(question_id)
        if q is None:
            raise ValueError("Вопрос не найден")
        if q.to_user_id != answering_user.id:
            raise PermissionError("Отвечать может только адресат вопроса")
        q.status = QuestionStatus.ANSWERED
        q.answer_text = text
        q.answered_at = datetime.utcnow()
        if self.scheduler is not None and self.scheduler.get_job(
                f"question_escalation:{question_id}"):
            self.scheduler.remove_job(f"question_escalation:{question_id}")
        if bool(await self.settings.get("questions.notify_asker_on_answer")):
            asker = await self.users.get_by_id(q.from_user_id)
            if asker is not None:
                try:
                    await self.bot.send_message(
                        chat_id=asker.telegram_id,
                        text=f"💬 Ответ на ваш вопрос:\n{html_escape(text)}")
                except Exception as exc:             # noqa: BLE001
                    logger.warning("Notify asker failed: %s", exc)
        await self.session.flush()
        return q


async def escalation_job(question_id: int, bot, session_factory) -> None:
    if session_factory is None:
        from bot.database.db import async_session_factory as session_factory  # noqa: PLW0127
    async with session_factory() as session:
        repo = QuestionRepository(session)
        settings = SettingService(session)
        q = await repo.get(question_id)
        if q is None or q.status != QuestionStatus.SENT:
            return
        q.status = QuestionStatus.ESCALATED
        q.escalated_at = datetime.utcnow()
        target_id = int(await settings.get("questions.escalation_receiver_user_id"))
        users = UserRepository(session)
        targets = ([await users.get_by_id(target_id)] if target_id
                   else await users.get_owners_and_partners())
        for user in targets:
            if user is None or not user.is_active:
                continue
            try:
                await bot.send_message(chat_id=user.telegram_id,
                                       text="⏰ Вопрос без ответа эскалирован")
            except Exception:                        # noqa: BLE001
                pass
        await session.commit()
