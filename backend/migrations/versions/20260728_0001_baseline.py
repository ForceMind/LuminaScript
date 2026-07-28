"""Create the LuminaScript baseline schema.

Revision ID: 20260728_0001
Revises:
"""
from typing import Optional

from alembic import op
import sqlalchemy as sa


revision: str = "20260728_0001"
down_revision: Optional[str] = None
branch_labels = None
depends_on = None


processing_status = sa.Enum(
    "PENDING",
    "GENERATING",
    "COMPLETED",
    "FAILED",
    name="processingstatus",
)


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("username", sa.String(), nullable=True),
        sa.Column("hashed_password", sa.String(), nullable=True),
        sa.Column("is_admin", sa.Integer(), nullable=True),
    )
    op.create_index("ix_users_id", "users", ["id"])
    op.create_index("ix_users_username", "users", ["username"], unique=True)

    op.create_table(
        "projects",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("title", sa.String(), nullable=True),
        sa.Column("logline", sa.String(), nullable=True),
        sa.Column("project_type", sa.String(), nullable=True),
        sa.Column("genre", sa.String(), nullable=True),
        sa.Column("total_tokens", sa.Integer(), nullable=True),
        sa.Column("status", processing_status, nullable=True),
        sa.Column("owner_id", sa.Integer(), nullable=True),
        sa.Column("global_context", sa.JSON(), nullable=True),
        sa.Column("next_step_cache", sa.JSON(), nullable=True),
        sa.Column("global_summary", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"]),
    )
    op.create_index("ix_projects_id", "projects", ["id"])
    op.create_index("ix_projects_title", "projects", ["title"])

    op.create_table(
        "login_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("ip_address", sa.String(), nullable=True),
        sa.Column("user_agent", sa.String(), nullable=True),
        sa.Column("location", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=True),
        sa.Column("timestamp", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
    )
    op.create_index("ix_login_logs_id", "login_logs", ["id"])

    op.create_table(
        "ai_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("project_id", sa.Integer(), nullable=True),
        sa.Column("action", sa.String(), nullable=True),
        sa.Column("prompt", sa.Text(), nullable=True),
        sa.Column("response", sa.Text(), nullable=True),
        sa.Column("tokens", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(), nullable=True),
        sa.Column("step_key", sa.String(), nullable=True),
        sa.Column("error_type", sa.String(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("attempt", sa.Integer(), nullable=True),
        sa.Column("timestamp", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
    )
    op.create_index("ix_ai_logs_id", "ai_logs", ["id"])

    op.create_table(
        "scenes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), nullable=True),
        sa.Column("scene_index", sa.Integer(), nullable=True),
        sa.Column("outline", sa.Text(), nullable=True),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("status", processing_status, nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.UniqueConstraint(
            "project_id",
            "scene_index",
            name="uq_scenes_project_scene_index",
        ),
    )
    op.create_index("ix_scenes_id", "scenes", ["id"])
    op.create_index("ix_scenes_scene_index", "scenes", ["scene_index"])


def downgrade() -> None:
    op.drop_index("ix_scenes_scene_index", table_name="scenes")
    op.drop_index("ix_scenes_id", table_name="scenes")
    op.drop_table("scenes")
    op.drop_index("ix_ai_logs_id", table_name="ai_logs")
    op.drop_table("ai_logs")
    op.drop_index("ix_login_logs_id", table_name="login_logs")
    op.drop_table("login_logs")
    op.drop_index("ix_projects_title", table_name="projects")
    op.drop_index("ix_projects_id", table_name="projects")
    op.drop_table("projects")
    op.drop_index("ix_users_username", table_name="users")
    op.drop_index("ix_users_id", table_name="users")
    op.drop_table("users")
    processing_status.drop(op.get_bind(), checkfirst=True)
