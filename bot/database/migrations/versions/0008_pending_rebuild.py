"""add pending_rebuild to tasks_config

Revision ID: 0008
Revises: 0007
Create Date: 2026-07-15 21:00:00.000000

"""
import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"


def upgrade() -> None:
    op.add_column(
        "tasks_config",
        sa.Column("pending_rebuild", sa.Boolean(), nullable=False,
                 server_default=sa.false()))


def downgrade() -> None:
    op.drop_column("tasks_config", "pending_rebuild")
