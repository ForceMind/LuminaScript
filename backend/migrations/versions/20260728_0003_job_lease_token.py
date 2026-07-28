"""Add generation job lease ownership tokens.

Revision ID: 20260728_0003
Revises: 20260728_0002
"""
from typing import Optional

from alembic import op
import sqlalchemy as sa


revision: str = "20260728_0003"
down_revision: Optional[str] = "20260728_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "generation_jobs",
        sa.Column("lock_token", sa.String(), nullable=True),
    )
    op.create_index(
        "ix_generation_jobs_lock_token",
        "generation_jobs",
        ["lock_token"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_generation_jobs_lock_token",
        table_name="generation_jobs",
    )
    op.drop_column("generation_jobs", "lock_token")
