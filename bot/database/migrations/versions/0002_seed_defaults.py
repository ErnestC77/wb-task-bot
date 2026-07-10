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
# не существует, поэтому ниже — локальная копия ключей, зафиксированных в бриф-плане
# (по одному представителю на каждую известную категорию registry).
SETTINGS_DEFAULTS: list[tuple[str, object, str, str]] = [
    ("general.timezone", "Europe/Moscow", "str", "general"),
    ("general.group_chat_id", 0, "int", "general"),
    ("general.page_size", 10, "int", "general"),
    ("general.telegram_retry_intervals", [30, 300, 1800], "json", "general"),
    ("article_check.batch_size", 15, "int", "article_check"),
    ("article_check.default_next_check_days", 3, "int", "article_check"),
    ("approval.timeout_hours", 24, "int", "approval"),
    ("reminders.first_after_hours", 3, "int", "reminders"),
    ("questions.default_receiver_user_id", 0, "int", "questions"),
    ("reports.weekday", 6, "int", "reports"),
    ("sync.conflict_policy", "admin_wins", "str", "sync"),
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
