"""add owner_notified_at to task_logs

Revision ID: 0006
Revises: 0005
Create Date: 2026-07-14 12:20:00.000000

"""
import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"


def upgrade() -> None:
    op.add_column(
        "task_logs",
        sa.Column("owner_notified_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("task_logs", "owner_notified_at")
