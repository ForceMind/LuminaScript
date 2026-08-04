"""Add operations, collaboration, versioning and template features.

Revision ID: 20260804_0004
Revises: 20260728_0003
"""
from typing import Optional

from alembic import op
import sqlalchemy as sa


revision: str = "20260804_0004"
down_revision: Optional[str] = "20260728_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # PostgreSQL persists SQLAlchemy Enum values in a native type. SQLite uses
    # a VARCHAR column and therefore needs no enum DDL here.
    if op.get_bind().dialect.name == "postgresql":
        op.execute("ALTER TYPE jobstatus ADD VALUE IF NOT EXISTS 'CANCELED'")

    with op.batch_alter_table("users") as batch_op:
        batch_op.add_column(
            sa.Column("daily_token_limit", sa.Integer(), server_default="0", nullable=False)
        )
        batch_op.add_column(
            sa.Column("monthly_token_limit", sa.Integer(), server_default="0", nullable=False)
        )

    with op.batch_alter_table("generation_jobs") as batch_op:
        batch_op.add_column(
            sa.Column("cancel_requested", sa.Boolean(), server_default=sa.false(), nullable=False)
        )

    op.create_table(
        "project_members",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(), nullable=False, server_default="viewer"),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "user_id", name="uq_project_members_project_user"),
    )
    op.create_index("ix_project_members_id", "project_members", ["id"])
    op.create_index("ix_project_members_project_id", "project_members", ["project_id"])
    op.create_index("ix_project_members_user_id", "project_members", ["user_id"])

    op.create_table(
        "project_versions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=False),
        sa.Column("label", sa.String(), nullable=False, server_default="手动快照"),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_project_versions_id", "project_versions", ["id"])
    op.create_index("ix_project_versions_project_id", "project_versions", ["project_id"])

    op.create_table(
        "prompt_templates",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("stage", sa.String(), nullable=False),
        sa.Column("project_type", sa.String(), nullable=False, server_default="all"),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_by", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_prompt_templates_id", "prompt_templates", ["id"])
    op.create_index("ix_prompt_templates_stage", "prompt_templates", ["stage"])
    op.create_index("ix_prompt_templates_project_type", "prompt_templates", ["project_type"])

    op.create_table(
        "backup_records",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("filename", sa.String(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(), nullable=False, server_default="completed"),
        sa.Column("backup_type", sa.String(), nullable=False, server_default="manual"),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("filename"),
    )
    op.create_index("ix_backup_records_id", "backup_records", ["id"])


def downgrade() -> None:
    op.drop_index("ix_backup_records_id", table_name="backup_records")
    op.drop_table("backup_records")
    op.drop_index("ix_prompt_templates_project_type", table_name="prompt_templates")
    op.drop_index("ix_prompt_templates_stage", table_name="prompt_templates")
    op.drop_index("ix_prompt_templates_id", table_name="prompt_templates")
    op.drop_table("prompt_templates")
    op.drop_index("ix_project_versions_project_id", table_name="project_versions")
    op.drop_index("ix_project_versions_id", table_name="project_versions")
    op.drop_table("project_versions")
    op.drop_index("ix_project_members_user_id", table_name="project_members")
    op.drop_index("ix_project_members_project_id", table_name="project_members")
    op.drop_index("ix_project_members_id", table_name="project_members")
    op.drop_table("project_members")

    with op.batch_alter_table("generation_jobs") as batch_op:
        batch_op.drop_column("cancel_requested")
    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_column("monthly_token_limit")
        batch_op.drop_column("daily_token_limit")
