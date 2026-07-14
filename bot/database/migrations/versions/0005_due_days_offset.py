"""add due_days_offset to tasks_config

Revision ID: 0005
Revises: 0004
Create Date: 2026-07-14 12:10:00.000000

"""
import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"


def upgrade() -> None:
    op.add_column(
        "tasks_config",
        sa.Column("due_days_offset", sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    op.drop_column("tasks_config", "due_days_offset")
