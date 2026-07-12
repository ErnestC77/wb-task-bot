from datetime import date, datetime, time
from enum import StrEnum

from sqlalchemy import (
    BigInteger, Boolean, Date, DateTime, ForeignKey, Integer, String, Text,
    Time, UniqueConstraint, func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from bot.database.db import Base


class Role(StrEnum):
    OWNER = "owner"
    PARTNER = "partner"
    MANAGER_WB = "manager_wb"
    LOGISTIC = "logistic"


class TaskStatus(StrEnum):
    CREATED = "created"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    WAITING_APPROVAL = "waiting_approval"
    APPROVED = "approved"
    AUTO_APPROVED = "auto_approved"
    POSTPONED = "postponed"
    PROBLEM = "problem"          # только для обычных задач, вручную через админ-операции
    OVERDUE = "overdue"
    CANCELLED = "cancelled"


class ScheduleType(StrEnum):
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    EVERY_N_DAYS = "every_n_days"
    CRON = "cron"


class TaskScenario(StrEnum):
    SIMPLE = "simple"
    ARTICLE_CHECK = "article_check"


class SessionStatus(StrEnum):
    ACTIVE = "active"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class CheckStatus(StrEnum):
    PENDING = "pending"
    CHECKED_NO_ACTION = "checked_no_action"
    ACTION_REQUIRED = "action_required"
    QUESTION = "question"


class QuestionStatus(StrEnum):
    CREATED = "created"
    SENT = "sent"
    DELIVERY_FAILED = "delivery_failed"
    ANSWERED = "answered"
    CLOSED = "closed"
    ESCALATED = "escalated"


class DeliveryStatus(StrEnum):
    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"
    RETRYING = "retrying"
    ABANDONED = "abandoned"


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now())


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    username: Mapped[str | None] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(128))
    role: Mapped[str] = mapped_column(String(32))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    private_chat_available: Mapped[bool] = mapped_column(Boolean, default=False)


class AdminPermission(TimestampMixin, Base):
    __tablename__ = "admin_permissions"
    __table_args__ = (UniqueConstraint("user_id", "permission_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    permission_key: Mapped[str] = mapped_column(String(64))
    is_allowed: Mapped[bool] = mapped_column(Boolean, default=True)
    granted_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))


class AdminAuditLog(Base):
    __tablename__ = "admin_audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    actor_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), index=True)
    action: Mapped[str] = mapped_column(String(64), index=True)
    entity_type: Mapped[str | None] = mapped_column(String(64))
    entity_id: Mapped[str | None] = mapped_column(String(64))
    setting_key: Mapped[str | None] = mapped_column(String(128))
    old_value_json: Mapped[str | None] = mapped_column(Text)
    new_value_json: Mapped[str | None] = mapped_column(Text)
    result: Mapped[str] = mapped_column(String(16), default="ok")  # ok|error
    ip_or_source: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class SystemSetting(TimestampMixin, Base):
    __tablename__ = "system_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    value_json: Mapped[str] = mapped_column(Text)          # JSON-сериализованное значение
    value_type: Mapped[str] = mapped_column(String(16))     # int|bool|str|json
    category: Mapped[str] = mapped_column(String(32), index=True)
    description: Mapped[str | None] = mapped_column(String(255))
    is_secret: Mapped[bool] = mapped_column(Boolean, default=False)
    is_editable: Mapped[bool] = mapped_column(Boolean, default=True)
    validation_rules_json: Mapped[str | None] = mapped_column(Text)
    updated_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))


class Topic(TimestampMixin, Base):
    __tablename__ = "topics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    topic_key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    topic_name: Mapped[str] = mapped_column(String(128))
    message_thread_id: Mapped[int | None] = mapped_column(Integer)
    event_types: Mapped[str | None] = mapped_column(String(255))  # csv: tasks,reports,...
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class TaskConfig(TimestampMixin, Base):
    __tablename__ = "tasks_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    external_task_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text)
    scenario: Mapped[str] = mapped_column(String(24), default=TaskScenario.SIMPLE)
    responsible_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    responsible_role: Mapped[str | None] = mapped_column(String(32))
    topic_id: Mapped[int | None] = mapped_column(ForeignKey("topics.id"))
    schedule_type: Mapped[str] = mapped_column(String(16))   # ScheduleType
    schedule_value: Mapped[str | None] = mapped_column(String(64))  # weekly: день; cron: выражение
    schedule_interval: Mapped[int | None] = mapped_column(Integer)  # для every_n_days
    # nullable=True указан явно: имя атрибута "time" совпадает с именем импортированного
    # типа datetime.time, из-за чего SQLAlchemy при резолве аннотации `time | None`
    # (typing.get_type_hints на этапе конфигурации маппера) видит уже связанный класс-атрибут
    # `time` вместо импортированного типа и теряет Optional — колонка иначе становится NOT NULL.
    time: Mapped[time | None] = mapped_column(Time, nullable=True)  # время создания задачи
    # nullable=True указан явно по той же причине, что и для `time` выше — атрибут `time`
    # в этом классе перекрывает импортированный тип для резолва всех аннотаций `time | None`.
    due_time: Mapped[time | None] = mapped_column(Time, nullable=True)  # срок «сегодня до HH:MM»
    first_run_date: Mapped[date | None] = mapped_column(Date)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)  # источник истины для recovery
    run_on_weekends: Mapped[bool] = mapped_column(Boolean, default=True)
    skip_holidays: Mapped[bool] = mapped_column(Boolean, default=False)
    need_approval: Mapped[bool] = mapped_column(Boolean, default=False)
    remind_after_hours: Mapped[int | None] = mapped_column(Integer)
    second_remind_after_hours: Mapped[int | None] = mapped_column(Integer)
    question_receiver_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    responsible_user: Mapped["User | None"] = relationship(
        lazy="joined", foreign_keys=[responsible_user_id])
    topic: Mapped["Topic | None"] = relationship(lazy="joined")


class TaskInstance(Base):
    __tablename__ = "task_instances"
    __table_args__ = (UniqueConstraint("config_id", "scheduled_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    config_id: Mapped[int] = mapped_column(ForeignKey("tasks_config.id"), index=True)
    responsible_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), index=True)
    topic_id: Mapped[int | None] = mapped_column(ForeignKey("topics.id"))
    status: Mapped[str] = mapped_column(String(24), default=TaskStatus.CREATED, index=True)

    # идемпотентность планировщика (10.2)
    scheduled_at: Mapped[datetime] = mapped_column(DateTime)
    scheduled_date: Mapped[date] = mapped_column(Date, index=True)
    schedule_key: Mapped[str] = mapped_column(String(96), index=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    due_at: Mapped[datetime | None] = mapped_column(DateTime)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime)
    auto_approved_at: Mapped[datetime | None] = mapped_column(DateTime)
    postponed_to: Mapped[datetime | None] = mapped_column(DateTime)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime)
    approval_deadline_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    reminders_sent: Mapped[int] = mapped_column(Integer, default=0)

    telegram_chat_id: Mapped[int | None] = mapped_column(BigInteger)
    telegram_message_id: Mapped[int | None] = mapped_column(BigInteger)

    # доставка (10.4)
    delivery_status: Mapped[str] = mapped_column(String(16), default=DeliveryStatus.PENDING)
    delivery_attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_delivery_error: Mapped[str | None] = mapped_column(Text)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    message_sent_at: Mapped[datetime | None] = mapped_column(DateTime)

    # snapshot конфигурации (10.5)
    title_snapshot: Mapped[str] = mapped_column(String(255))
    description_snapshot: Mapped[str | None] = mapped_column(Text)
    responsible_name_snapshot: Mapped[str | None] = mapped_column(String(128))
    responsible_telegram_id_snapshot: Mapped[int | None] = mapped_column(BigInteger)
    need_approval_snapshot: Mapped[bool] = mapped_column(Boolean, default=False)
    approval_timeout_hours_snapshot: Mapped[int] = mapped_column(Integer, default=24)
    remind_after_hours_snapshot: Mapped[int | None] = mapped_column(Integer)
    second_remind_after_hours_snapshot: Mapped[int | None] = mapped_column(Integer)
    question_receiver_snapshot: Mapped[int | None] = mapped_column(Integer)  # user_id получателя
    topic_snapshot: Mapped[int | None] = mapped_column(Integer)              # message_thread_id
    article_batch_size_snapshot: Mapped[int | None] = mapped_column(Integer)
    scenario_snapshot: Mapped[str] = mapped_column(String(24), default=TaskScenario.SIMPLE)
    settings_version: Mapped[int] = mapped_column(Integer, default=1)

    config: Mapped["TaskConfig"] = relationship(lazy="joined")
    responsible_user: Mapped["User | None"] = relationship(lazy="joined")


class TaskLog(Base):
    __tablename__ = "task_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_instance_id: Mapped[int] = mapped_column(ForeignKey("task_instances.id"), index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(64))
    old_status: Mapped[str | None] = mapped_column(String(24))
    new_status: Mapped[str | None] = mapped_column(String(24))
    comment: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Article(TimestampMixin, Base):
    __tablename__ = "articles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    article: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    product_name: Mapped[str | None] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    responsible_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    responsible_role: Mapped[str | None] = mapped_column(String(32))
    source: Mapped[str] = mapped_column(String(24), default="google_sheets")  # google_sheets|manual
    source_updated_at: Mapped[datetime | None] = mapped_column(DateTime)


class ArticleCheckSession(TimestampMixin, Base):
    __tablename__ = "article_check_sessions"
    __table_args__ = (UniqueConstraint("task_instance_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_instance_id: Mapped[int] = mapped_column(ForeignKey("task_instances.id"), index=True)
    responsible_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    status: Mapped[str] = mapped_column(String(16), default=SessionStatus.ACTIVE, index=True)
    total_articles: Mapped[int] = mapped_column(Integer, default=0)
    checked_articles: Mapped[int] = mapped_column(Integer, default=0)
    action_required_count: Mapped[int] = mapped_column(Integer, default=0)
    questions_count: Mapped[int] = mapped_column(Integer, default=0)
    current_batch: Mapped[int] = mapped_column(Integer, default=1)
    batch_size: Mapped[int] = mapped_column(Integer, default=15)
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)
    version: Mapped[int] = mapped_column(Integer, default=1)


class ArticleCheckItem(TimestampMixin, Base):
    __tablename__ = "article_check_items"
    __table_args__ = (UniqueConstraint("check_session_id", "article_snapshot"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    check_session_id: Mapped[int] = mapped_column(
        ForeignKey("article_check_sessions.id"), index=True)
    article_id: Mapped[int | None] = mapped_column(ForeignKey("articles.id"))
    article_snapshot: Mapped[str] = mapped_column(String(32))
    product_name_snapshot: Mapped[str | None] = mapped_column(String(255))
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    check_status: Mapped[str] = mapped_column(
        String(24), default=CheckStatus.PENDING, index=True)
    checked_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    checked_at: Mapped[datetime | None] = mapped_column(DateTime)
    version: Mapped[int] = mapped_column(Integer, default=1)


class ArticleCategory(TimestampMixin, Base):
    __tablename__ = "article_categories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class ProblemType(TimestampMixin, Base):
    __tablename__ = "problem_types"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    require_comment: Mapped[bool] = mapped_column(Boolean, default=False)
    default_next_check_days: Mapped[int | None] = mapped_column(Integer)


class DecisionType(TimestampMixin, Base):
    __tablename__ = "decision_types"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    require_comment: Mapped[bool] = mapped_column(Boolean, default=False)
    default_next_check_days: Mapped[int | None] = mapped_column(Integer)
    allowed_categories_json: Mapped[str | None] = mapped_column(Text)  # JSON-список id категорий


class ProblemDecisionLink(Base):
    __tablename__ = "problem_decision_links"
    __table_args__ = (UniqueConstraint("problem_type_id", "decision_type_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    problem_type_id: Mapped[int] = mapped_column(ForeignKey("problem_types.id"), index=True)
    decision_type_id: Mapped[int] = mapped_column(ForeignKey("decision_types.id"))


class ArticleAction(TimestampMixin, Base):
    __tablename__ = "article_actions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    article_check_item_id: Mapped[int | None] = mapped_column(
        ForeignKey("article_check_items.id"), index=True)
    task_instance_id: Mapped[int | None] = mapped_column(
        ForeignKey("task_instances.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    article: Mapped[str] = mapped_column(String(32), index=True)
    category_id: Mapped[int | None] = mapped_column(ForeignKey("article_categories.id"))
    problem_type_id: Mapped[int | None] = mapped_column(ForeignKey("problem_types.id"))
    decision_type_id: Mapped[int | None] = mapped_column(ForeignKey("decision_types.id"))
    # name-snapshots: история не ломается при переименовании справочников
    category_name_snapshot: Mapped[str | None] = mapped_column(String(64))
    problem_name_snapshot: Mapped[str | None] = mapped_column(String(128))
    decision_name_snapshot: Mapped[str | None] = mapped_column(String(128))
    comment: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(24), default="open")  # open|done|cancelled
    next_check_date: Mapped[date | None] = mapped_column(Date, index=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)


class TaskQuestion(Base):
    __tablename__ = "task_questions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_instance_id: Mapped[int] = mapped_column(ForeignKey("task_instances.id"), index=True)
    article_check_item_id: Mapped[int | None] = mapped_column(
        ForeignKey("article_check_items.id"))
    from_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    to_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    question_text: Mapped[str] = mapped_column(Text)
    answer_text: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(24), default=QuestionStatus.CREATED, index=True)
    telegram_chat_id: Mapped[int | None] = mapped_column(BigInteger)
    telegram_message_id: Mapped[int | None] = mapped_column(BigInteger)
    delivery_status: Mapped[str] = mapped_column(String(16), default=DeliveryStatus.PENDING)
    delivery_attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_delivery_error: Mapped[str | None] = mapped_column(Text)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    answered_at: Mapped[datetime | None] = mapped_column(DateTime)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime)
    escalated_at: Mapped[datetime | None] = mapped_column(DateTime)


class DeliveryAttempt(Base):
    __tablename__ = "delivery_attempts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    entity_type: Mapped[str] = mapped_column(String(32), index=True)  # task_instance|task_question|reminder|report
    entity_id: Mapped[int] = mapped_column(Integer, index=True)
    chat_id: Mapped[int | None] = mapped_column(BigInteger)
    message_thread_id: Mapped[int | None] = mapped_column(Integer)
    attempt_number: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(16))  # sent|failed
    error_text: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
