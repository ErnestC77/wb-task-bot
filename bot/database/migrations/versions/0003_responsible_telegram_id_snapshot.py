"""add responsible_telegram_id_snapshot to task_instances

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-12 17:40:00.000000

"""
import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"


def upgrade() -> None:
    op.add_column(
        "task_instances",
        sa.Column("responsible_telegram_id_snapshot", sa.BigInteger(), nullable=True))


def downgrade() -> None:
    op.drop_column("task_instances", "responsible_telegram_id_snapshot")
