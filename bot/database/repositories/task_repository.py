from datetime import date, datetime

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import lazyload

from bot.database.models import (
    DeliveryStatus, TaskConfig, TaskInstance, TaskLog, TaskStatus,
)

OPEN_STATUSES: frozenset[str] = frozenset(
    {TaskStatus.CREATED, TaskStatus.IN_PROGRESS, TaskStatus.POSTPONED})


class TaskRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def upsert_config(self, data: dict) -> TaskConfig:
        cfg = await self.session.scalar(select(TaskConfig).where(
            TaskConfig.external_task_id == data["external_task_id"]))
        if cfg is None:
            cfg = TaskConfig(**data)
            self.session.add(cfg)
        else:
            for key, value in data.items():
                setattr(cfg, key, value)   # data формируется кодом, не callback'ом
        await self.session.flush()
        return cfg

    async def get_active_configs(self) -> list[TaskConfig]:
        return list(await self.session.scalars(
            select(TaskConfig).where(TaskConfig.is_active.is_(True))))

    async def get_pending_rebuild(self) -> list[TaskConfig]:
        return list(await self.session.scalars(
            select(TaskConfig).where(TaskConfig.pending_rebuild.is_(True))))

    async def get_all_configs(self, include_inactive: bool = False) -> list[TaskConfig]:
        """Task 27: список шаблонов в админ-панели (в отличие от get_active_configs,
        нужны и неактивные — та же пара методов, что TopicRepository.get_all /
        UserRepository.get_all)."""
        stmt = select(TaskConfig)
        if not include_inactive:
            stmt = stmt.where(TaskConfig.is_active.is_(True))
        return list(await self.session.scalars(stmt))

    async def get_active_configs_not_in(self, external_ids: set[str]) -> list[TaskConfig]:
        """Task 22: активные конфиги, отсутствующие в свежей выгрузке Sheets."""
        stmt = select(TaskConfig).where(TaskConfig.is_active.is_(True))
        if external_ids:
            stmt = stmt.where(TaskConfig.external_task_id.not_in(external_ids))
        return list(await self.session.scalars(stmt))

    async def get_config(self, config_id: int) -> TaskConfig | None:
        return await self.session.get(TaskConfig, config_id)

    async def get_config_by_external_id(self, ext_id: str) -> TaskConfig | None:
        return await self.session.scalar(select(TaskConfig).where(
            TaskConfig.external_task_id == ext_id))

    async def create_instance_idempotent(
        self, config: TaskConfig, scheduled_at: datetime,
        due_at: datetime | None, snapshot: dict,
        responsible_user_id: int | None = ...,
    ) -> TaskInstance | None:
        # Идемпотентность держится на UniqueConstraint(config_id, scheduled_at)
        # у TaskInstance в models.py — это не тестовый фикс, а гарантия на
        # уровне схемы БД (IntegrityError ловится ниже как «дубль планового запуска»).
        # responsible_user_id по умолчанию (Ellipsis, не передан вызывающим) —
        # берём из config, как раньше; TaskService.create_instance_for передаёт
        # его явно, т.к. может авто-подобрать по responsible_role (см.
        # TaskService.resolve_responsible_user), что может отличаться от
        # config.responsible_user_id.
        if responsible_user_id is ...:
            responsible_user_id = config.responsible_user_id
        inst = TaskInstance(
            config_id=config.id,
            responsible_user_id=responsible_user_id,
            topic_id=config.topic_id,
            scheduled_at=scheduled_at,
            scheduled_date=scheduled_at.date(),
            schedule_key=f"{config.id}:{scheduled_at.isoformat()}",
            due_at=due_at,
            delivery_status=DeliveryStatus.PENDING,
            **snapshot,
        )
        try:
            async with self.session.begin_nested():   # savepoint
                self.session.add(inst)
                await self.session.flush()
        except IntegrityError:
            return None                                # дубль планового запуска
        return inst

    async def get_instance(self, instance_id: int) -> TaskInstance | None:
        return await self.session.get(TaskInstance, instance_id)

    async def transition_status(
        self, instance_id: int, expected_statuses: list[str], new_status: str,
        actor_user_id: int | None, action: str, comment: str | None = None,
        **timestamps: object,
    ) -> TaskInstance | None:
        """Безопасный переход: SELECT FOR UPDATE + проверка + conditional UPDATE + TaskLog.

        В PostgreSQL with_for_update блокирует строку (гонки callback/scheduler);
        в SQLite (тесты) это no-op, но conditional WHERE по статусу сохраняется.

        `lazyload(TaskInstance.config)`/`lazyload(TaskInstance.responsible_user)`
        — КРИТИЧЕСКИЙ фикс (найден интеграционным тестом на реальном Postgres,
        Task 38): оба поля объявлены `lazy="joined"` на модели, а `TaskConfig`
        (загружаемый вместе через join) сам эагерно джойнит ЕЩЁ `responsible_user`
        и `topic`. PostgreSQL физически запрещает `FOR UPDATE` на nullable-стороне
        LEFT OUTER JOIN ("FeatureNotSupportedError: FOR UPDATE cannot be applied
        to the nullable side of an outer join") — с эагер-джойнами по умолчанию
        ЛЮБОЙ вызов transition_status (единственный безопасный механизм смены
        статуса задачи во всём проекте) падал бы на реальной БД. В SQLite это
        молча работало, потому что FOR UPDATE там — no-op, поэтому баг был
        невидим все предыдущие тесты/задачи. `session.refresh(row)` ниже НЕ
        передаёт `with_for_update`, поэтому корректно восстанавливает
        `row.config`/`row.responsible_user` обычным (без FOR UPDATE) джойном —
        поведение для вызывающего кода (например, `auto_approve_job`, который
        читает `got.responsible_user.telegram_id`) не меняется.
        """
        row = await self.session.scalar(
            select(TaskInstance).where(TaskInstance.id == instance_id)
            .options(lazyload(TaskInstance.config), lazyload(TaskInstance.responsible_user))
            .with_for_update())
        if row is None or row.status not in expected_statuses:
            return None
        old_status = row.status
        result = await self.session.execute(
            update(TaskInstance)
            .where(TaskInstance.id == instance_id,
                   TaskInstance.status.in_(expected_statuses))
            .values(status=new_status, **timestamps))
        if result.rowcount == 0:
            return None
        self.session.add(TaskLog(task_instance_id=instance_id, user_id=actor_user_id,
                                 action=action, old_status=old_status,
                                 new_status=new_status, comment=comment))
        await self.session.flush()
        # attribute_names ОБЯЗАТЕЛЕН: refresh() без него повторно наполняет
        # только УЖЕ загруженные атрибуты — а config/responsible_user намеренно
        # не загружены (lazyload выше). Без явного attribute_names они остались
        # бы unloaded, и последующий доступ вызывающего кода (например,
        # `auto_approve_job`: `got.responsible_user.telegram_id`) попытался бы
        # синхронный lazy-load вне greenlet-контекста async-сессии —
        # `MissingGreenlet` (найдено этим же фиксом при прогоне полного
        # юнит-набора после исправления Task 38, регресс на SQLite).
        await self.session.refresh(row, attribute_names=["config", "responsible_user"])
        return row

    async def set_message_info(self, instance_id: int, chat_id: int,
                               message_id: int) -> None:
        await self.session.execute(update(TaskInstance)
                                   .where(TaskInstance.id == instance_id)
                                   .values(telegram_chat_id=chat_id,
                                           telegram_message_id=message_id))
        await self.session.flush()

    async def get_open_instances_for_user(self, user_id: int) -> list[TaskInstance]:
        return list(await self.session.scalars(
            select(TaskInstance).where(
                TaskInstance.responsible_user_id == user_id,
                TaskInstance.status.in_(OPEN_STATUSES)).order_by(TaskInstance.due_at)))

    async def get_by_status(self, status: str) -> list[TaskInstance]:
        return list(await self.session.scalars(
            select(TaskInstance).where(TaskInstance.status == status)
            .order_by(TaskInstance.due_at)))

    async def get_by_scheduled_date(self, day: date) -> list[TaskInstance]:
        """Task 20: /today — все инстансы, запланированные на конкретный день
        (фильтрация по actor'у — на уровне handler'а, не здесь)."""
        return list(await self.session.scalars(
            select(TaskInstance).where(TaskInstance.scheduled_date == day)
            .order_by(TaskInstance.due_at)))

    async def get_all_open_instances(self) -> list[TaskInstance]:
        """Task 20: /my_tasks для owner/partner — открытые задачи ВСЕХ сотрудников
        (правило плана: owner и partner видят все задачи)."""
        return list(await self.session.scalars(
            select(TaskInstance).where(TaskInstance.status.in_(OPEN_STATUSES))
            .order_by(TaskInstance.due_at)))

    async def get_waiting_approval(self) -> list[TaskInstance]:
        return await self.get_by_status(TaskStatus.WAITING_APPROVAL)

    async def get_open_with_reminders(self) -> list[TaskInstance]:
        return list(await self.session.scalars(
            select(TaskInstance).where(
                TaskInstance.status.in_(OPEN_STATUSES),
                TaskInstance.remind_after_hours_snapshot.is_not(None))))

    async def get_pending_delivery(self) -> list[TaskInstance]:
        return list(await self.session.scalars(
            select(TaskInstance).where(TaskInstance.delivery_status.in_(
                [DeliveryStatus.PENDING, DeliveryStatus.FAILED, DeliveryStatus.RETRYING]))))

    async def get_sent_unlogged(self) -> list[TaskInstance]:
        """Часть Б: отправленные в Telegram, но ещё не выгруженные в лист
        «Журнал отправок» (sheet_logged_at IS NULL)."""
        return list(await self.session.scalars(
            select(TaskInstance).where(
                TaskInstance.delivery_status == DeliveryStatus.SENT,
                TaskInstance.sheet_logged_at.is_(None))
            .order_by(TaskInstance.message_sent_at)))

    async def get_unnotified_status_logs(self, statuses: list[str]) -> list[TaskLog]:
        """Часть Г: записи TaskLog с new_status из переданного списка, по
        которым Telegram-уведомление ещё не отправлялось (owner_notified_at
        IS NULL). Список статусов передаёт вызывающий job — значение настройки
        status_notifications.statuses (bot/services/status_notification_service.py)."""
        return list(await self.session.scalars(
            select(TaskLog).where(
                TaskLog.new_status.in_(statuses),
                TaskLog.owner_notified_at.is_(None))
            .order_by(TaskLog.id)))

    async def get_unlogged_status_logs(self) -> list[TaskLog]:
        """Часть Д: ВСЕ записи TaskLog (без фильтра по статусу — полная
        история), ещё не выгруженные в лист «История статусов»
        (sheet_logged_at IS NULL)."""
        return list(await self.session.scalars(
            select(TaskLog).where(TaskLog.sheet_logged_at.is_(None))
            .order_by(TaskLog.id)))
