"""Add durable generation jobs.

Revision ID: 20260728_0002
Revises: 20260728_0001
"""
from typing import Optional

from alembic import op
import sqlalchemy as sa


revision: str = "20260728_0002"
down_revision: Optional[str] = "20260728_0001"
branch_labels = None
depends_on = None


job_status = sa.Enum(
    "QUEUED",
    "RUNNING",
    "COMPLETED",
    "FAILED",
    name="jobstatus",
)


def upgrade() -> None:
    op.create_table(
        "generation_jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), nullable=True),
        sa.Column("kind", sa.String(), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Column("status", job_status, nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=True),
        sa.Column("max_attempts", sa.Integer(), nullable=True),
        sa.Column("available_at", sa.String(), nullable=True),
        sa.Column("locked_at", sa.String(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.String(), nullable=True),
        sa.Column("updated_at", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
    )
    op.create_index("ix_generation_jobs_id", "generation_jobs", ["id"])
    op.create_index(
        "ix_generation_jobs_project_id",
        "generation_jobs",
        ["project_id"],
    )
    op.create_index("ix_generation_jobs_kind", "generation_jobs", ["kind"])
    op.create_index("ix_generation_jobs_status", "generation_jobs", ["status"])
    op.create_index(
        "ix_generation_jobs_available_at",
        "generation_jobs",
        ["available_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_generation_jobs_available_at",
        table_name="generation_jobs",
    )
    op.drop_index("ix_generation_jobs_status", table_name="generation_jobs")
    op.drop_index("ix_generation_jobs_kind", table_name="generation_jobs")
    op.drop_index(
        "ix_generation_jobs_project_id",
        table_name="generation_jobs",
    )
    op.drop_index("ix_generation_jobs_id", table_name="generation_jobs")
    op.drop_table("generation_jobs")
    job_status.drop(op.get_bind(), checkfirst=True)
