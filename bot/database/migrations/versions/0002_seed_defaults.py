"""seed defaults: settings, topics, dictionaries, main task

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-10 17:20:00.000000

"""
import json

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"


settings_table = sa.table(
    "system_settings",
    sa.column("key", sa.String), sa.column("value_json", sa.Text),
    sa.column("value_type", sa.String), sa.column("category", sa.String),
    sa.column("description", sa.String), sa.column("is_secret", sa.Boolean),
    sa.column("is_editable", sa.Boolean),
)

# ВАЖНО: миграция не импортирует код приложения — фиксирует defaults на момент ревизии.
# Task 7 определит полноценный SETTINGS_REGISTRY в коде; на момент этой ревизии он ещё
# не существует, поэтому ниже — локальная копия ПОЛНОГО набора ключей registry
# (94 ключа, 11 категорий), зафиксированных в бриф-плане.
SETTINGS_DEFAULTS: list[tuple[str, object, str, str]] = [
    # general (14)
    ("general.timezone", "Europe/Moscow", "str", "general"),
    ("general.datetime_format", "%d.%m.%Y %H:%M", "str", "general"),
    ("general.language", "ru", "str", "general"),
    ("general.group_chat_id", 0, "int", "general"),
    ("general.bot_enabled", True, "bool", "general"),
    ("general.maintenance_mode", False, "bool", "general"),
    ("general.page_size", 10, "int", "general"),
    ("general.max_comment_length", 500, "int", "general"),
    ("general.max_question_length", 1000, "int", "general"),
    ("general.max_task_title_length", 255, "int", "general"),
    ("general.log_retention_days", 90, "int", "general"),
    ("general.verbose_business_logging", False, "bool", "general"),
    ("general.telegram_retry_count", 3, "int", "general"),
    ("general.telegram_retry_intervals", [30, 300, 1800], "json", "general"),
    # article_check (19)
    ("article_check.batch_size", 15, "int", "article_check"),
    ("article_check.batch_size_min", 5, "int", "article_check"),
    ("article_check.batch_size_max", 50, "int", "article_check"),
    ("article_check.source", "google_sheets", "str", "article_check"),
    ("article_check.sheet_name", "Articles", "str", "article_check"),
    ("article_check.show_product_name", True, "bool", "article_check"),
    ("article_check.show_extra_metrics", False, "bool", "article_check"),
    ("article_check.sort_order", "sort_order", "str", "article_check"),
    ("article_check.allow_finish_with_open_questions", True, "bool", "article_check"),
    ("article_check.require_action_for_action_required", True, "bool", "article_check"),
    ("article_check.default_next_check_days", 3, "int", "article_check"),
    ("article_check.next_check_min_days", 1, "int", "article_check"),
    ("article_check.next_check_max_days", 60, "int", "article_check"),
    ("article_check.allow_prev_batch", True, "bool", "article_check"),
    ("article_check.allow_change_result", True, "bool", "article_check"),
    ("article_check.allow_manual_article_add", False, "bool", "article_check"),
    ("article_check.include_archived", False, "bool", "article_check"),
    ("article_check.session_list_change_policy", "keep_snapshot", "str", "article_check"),
    ("article_check.allow_finish_batch_with_pending", False, "bool", "article_check"),
    # approval (10)
    ("approval.timeout_hours", 24, "int", "approval"),
    ("approval.auto_approve_enabled", True, "bool", "approval"),
    ("approval.approvers_mode", "first", "str", "approval"),
    ("approval.require_two_approvals", False, "bool", "approval"),
    ("approval.topic_key", "owners_decisions", "str", "approval"),
    ("approval.send_private", False, "bool", "approval"),
    ("approval.notify_on_approve", True, "bool", "approval"),
    ("approval.notify_on_auto_approve", True, "bool", "approval"),
    ("approval.allow_return_to_work", True, "bool", "approval"),
    ("approval.return_comment_required", True, "bool", "approval"),
    # reminders (14)
    ("reminders.first_after_hours", 3, "int", "reminders"),
    ("reminders.second_after_hours", 6, "int", "reminders"),
    ("reminders.extra_enabled", False, "bool", "reminders"),
    ("reminders.repeat_interval_hours", 4, "int", "reminders"),
    ("reminders.max_count", 2, "int", "reminders"),
    ("reminders.targets", ["topic"], "json", "reminders"),
    ("reminders.quiet_hours_start", "22:00", "str", "reminders"),
    ("reminders.quiet_hours_end", "08:00", "str", "reminders"),
    ("reminders.shift_night_to_morning", True, "bool", "reminders"),
    ("reminders.text_template", "⏰ Напоминание: задача «{title}» не завершена", "str", "reminders"),
    ("reminders.overdue_enabled", True, "bool", "reminders"),
    ("reminders.overdue_after_hours", 24, "int", "reminders"),
    ("reminders.escalation_enabled", False, "bool", "reminders"),
    ("reminders.escalation_targets", ["owner"], "json", "reminders"),
    # questions (8)
    ("questions.default_receiver_user_id", 0, "int", "questions"),
    ("questions.fallback_receiver_user_id", 0, "int", "questions"),
    ("questions.escalation_hours", 4, "int", "questions"),
    ("questions.escalation_receiver_user_id", 0, "int", "questions"),
    ("questions.notify_asker_on_answer", True, "bool", "questions"),
    ("questions.allow_complete_with_open_questions", True, "bool", "questions"),
    ("questions.route_by_topic", {}, "json", "questions"),
    ("questions.route_by_category", {}, "json", "questions"),
    # reports (12)
    ("reports.weekday", 6, "int", "reports"),
    ("reports.time", "20:00", "str", "reports"),
    ("reports.period_days", 7, "int", "reports"),
    ("reports.topic_key", "reports", "str", "reports"),
    ("reports.private_receiver_ids", [], "json", "reports"),
    ("reports.show_overdue", True, "bool", "reports"),
    ("reports.show_auto_approved", True, "bool", "reports"),
    ("reports.show_problem_articles", True, "bool", "reports"),
    ("reports.show_open_questions", True, "bool", "reports"),
    ("reports.show_expired_checks", True, "bool", "reports"),
    ("reports.show_per_employee", True, "bool", "reports"),
    ("reports.format", "full", "str", "reports"),
    # sync (8)
    ("sync.spreadsheet_id", "", "str", "sync"),
    ("sync.sheet_users", "Users", "str", "sync"),
    ("sync.sheet_topics", "Topics", "str", "sync"),
    ("sync.sheet_tasks", "Tasks_Config", "str", "sync"),
    ("sync.auto_enabled", False, "bool", "sync"),
    ("sync.interval_minutes", 60, "int", "sync"),
    ("sync.conflict_policy", "admin_wins", "str", "sync"),
    ("sync.dry_run_default", True, "bool", "sync"),
    # delivery_log (2)
    ("delivery_log.enabled", False, "bool", "delivery_log"),
    ("delivery_log.interval_minutes", 60, "int", "delivery_log"),
    # status_notifications (4)
    ("status_notifications.enabled", False, "bool", "status_notifications"),
    ("status_notifications.targets", ["owner"], "json", "status_notifications"),
    ("status_notifications.interval_minutes", 5, "int", "status_notifications"),
    ("status_notifications.statuses",
     ["in_progress", "completed", "problem", "overdue"], "json", "status_notifications"),
    # status_history_log (2)
    ("status_history_log.enabled", False, "bool", "status_history_log"),
    ("status_history_log.interval_minutes", 60, "int", "status_history_log"),
    # internal (1)
    ("internal.settings_version", 1, "int", "internal"),
]


def upgrade() -> None:
    op.bulk_insert(settings_table, [
        {"key": k, "value_json": json.dumps(v), "value_type": t, "category": c,
         "description": None, "is_secret": False,
         "is_editable": not k.startswith("internal.")}
        for k, v, t, c in SETTINGS_DEFAULTS
    ])

    topics = sa.table("topics", sa.column("topic_key", sa.String),
                      sa.column("topic_name", sa.String), sa.column("is_active", sa.Boolean))
    op.bulk_insert(topics, [
        {"topic_key": "general", "topic_name": "Общий", "is_active": True},
        {"topic_key": "goods", "topic_name": "Управление товарами", "is_active": True},
        {"topic_key": "logistics", "topic_name": "Логистика", "is_active": True},
        {"topic_key": "reports", "topic_name": "Отчеты", "is_active": True},
        {"topic_key": "ideas", "topic_name": "Идеи", "is_active": True},
        {"topic_key": "owners_decisions", "topic_name": "Решения собственников", "is_active": True},
    ])

    cats = sa.table("article_categories", sa.column("name", sa.String),
                    sa.column("sort_order", sa.Integer), sa.column("is_active", sa.Boolean))
    op.bulk_insert(cats, [
        {"name": n, "sort_order": i, "is_active": True}
        for i, n in enumerate(["Тест", "Перспективный", "Победитель", "Ликвидация"])
    ])

    problems = sa.table("problem_types", sa.column("name", sa.String),
                        sa.column("sort_order", sa.Integer), sa.column("is_active", sa.Boolean),
                        sa.column("require_comment", sa.Boolean),
                        sa.column("default_next_check_days", sa.Integer))
    op.bulk_insert(problems, [
        {"name": n, "sort_order": i, "is_active": True,
         "require_comment": n == "другое", "default_next_check_days": 3}
        for i, n in enumerate([
            "падают корзины", "нет роста корзин", "высокие остатки", "высокий CPL",
            "упала прибыль", "падает рентабельность",
            "нет новых действий более 5 дней", "другое",
        ])
    ])

    decisions = sa.table("decision_types", sa.column("name", sa.String),
                         sa.column("sort_order", sa.Integer), sa.column("is_active", sa.Boolean),
                         sa.column("require_comment", sa.Boolean),
                         sa.column("default_next_check_days", sa.Integer))
    op.bulk_insert(decisions, [
        {"name": n, "sort_order": i, "is_active": True,
         "require_comment": n == "другое", "default_next_check_days": 3}
        for i, n in enumerate([
            "улучшить отзывы", "начислить баллы за отзывы", "запустить CPC-рекламу",
            "повысить ставку рекламы", "снизить ставку рекламы",
            "протестировать новую обложку", "изменить цену", "включить акцию",
            "перевести в Ликвидацию", "заказать поставку", "другое",
        ])
    ])

    # рекомендуемые решения (пример связок; полный маппинг — по здравому смыслу бизнеса)
    op.execute("""
        INSERT INTO problem_decision_links (problem_type_id, decision_type_id)
        SELECT p.id, d.id FROM problem_types p, decision_types d
        WHERE (p.name = 'высокий CPL' AND d.name IN ('снизить ставку рекламы', 'протестировать новую обложку'))
           OR (p.name = 'падают корзины' AND d.name IN ('улучшить отзывы', 'запустить CPC-рекламу'))
           OR (p.name = 'высокие остатки' AND d.name IN ('включить акцию', 'изменить цену'))
    """)

    # основная задача: раз в 2 дня, article_check, срок до 12:00
    op.execute("""
        INSERT INTO tasks_config (external_task_id, title, description, scenario,
            responsible_role, topic_id, schedule_type, schedule_interval, time, due_time,
            run_on_weekends, skip_holidays, need_approval, remind_after_hours,
            second_remind_after_hours, is_active, created_at, updated_at)
        SELECT 'articles_check_all',
            'Проверить все артикулы и выявить артикулы, по которым нужны действия',
            'Проверить все активные артикулы и выявить товары, по которым требуются действия: падают корзины; отсутствует рост корзин; высокие остатки; высокий CPL; упала прибыль; снижается рентабельность; нет новых действий более 5 дней; другие отклонения.',
            'article_check', 'manager_wb', t.id, 'every_n_days', 2,
            '09:00', '12:00', true, false, true, 3, 6, true, now(), now()
        FROM topics t WHERE t.topic_key = 'goods'
    """)


def downgrade() -> None:
    for table in ("problem_decision_links", "decision_types", "problem_types",
                  "article_categories", "system_settings"):
        op.execute(f"DELETE FROM {table}")
    op.execute("DELETE FROM tasks_config WHERE external_task_id = 'articles_check_all'")
    op.execute("DELETE FROM topics")
