"""add sheet_logged_at to task_instances

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-14 12:00:00.000000

"""
import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"


def upgrade() -> None:
    op.add_column(
        "task_instances",
        sa.Column("sheet_logged_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("task_instances", "sheet_logged_at")
