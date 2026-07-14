"""add sheet_logged_at to task_logs

Revision ID: 0007
Revises: 0006
Create Date: 2026-07-14 12:30:00.000000

"""
import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"


def upgrade() -> None:
    op.add_column(
        "task_logs",
        sa.Column("sheet_logged_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("task_logs", "sheet_logged_at")
