"""Preserve AI audit logs on project deletion and index hot database paths.

Revision ID: 20260906_0008
Revises: 20260904_0007
"""
from alembic import op
import sqlalchemy as sa
from typing import Optional


revision = "20260906_0008"
down_revision = "20260904_0007"
branch_labels = None
depends_on = None


_FK_NAMING = {
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
}


def _replace_project_foreign_key(*, ondelete: Optional[str]) -> None:
    """Handle old unnamed SQLite FKs and named PostgreSQL FKs safely."""
    bind = op.get_bind()
    foreign_keys = sa.inspect(bind).get_foreign_keys("ai_logs")
    project_foreign_key = next(
        (
            foreign_key
            for foreign_key in foreign_keys
            if foreign_key.get("constrained_columns") == ["project_id"]
            and foreign_key.get("referred_table") == "projects"
        ),
        None,
    )
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table(
            "ai_logs",
            recreate="always",
            naming_convention=_FK_NAMING,
        ) as batch:
            # upgrade_admin historically created project_id without an FK.
            # Only drop the reflected project constraint; user and billing
            # constraints remain part of the rebuilt table.
            if project_foreign_key:
                batch.drop_constraint("fk_ai_logs_project_id_projects", type_="foreignkey")
            batch.create_foreign_key(
                "fk_ai_logs_project_id_projects",
                "projects",
                ["project_id"],
                ["id"],
                ondelete=ondelete,
            )
        return

    if not project_foreign_key or not project_foreign_key.get("name"):
        raise RuntimeError("无法识别 ai_logs.project_id 的外键，迁移已停止以保护审计日志")
    op.drop_constraint(project_foreign_key["name"], "ai_logs", type_="foreignkey")
    op.create_foreign_key(
        "fk_ai_logs_project_id_projects",
        "ai_logs",
        "projects",
        ["project_id"],
        ["id"],
        ondelete=ondelete,
    )


def upgrade() -> None:
    # Historical SQLite databases may contain dangling ids because SQLite FK
    # enforcement was previously connection-local and disabled by default.
    # Preserve the log and only remove the invalid project reference before the
    # table rebuild validates its replacement constraint.
    op.execute(
        "UPDATE ai_logs SET project_id = NULL "
        "WHERE project_id IS NOT NULL "
        "AND NOT EXISTS (SELECT 1 FROM projects WHERE projects.id = ai_logs.project_id)"
    )
    _replace_project_foreign_key(ondelete="SET NULL")
    op.create_index(
        "ix_ai_logs_billing_identity_timestamp",
        "ai_logs",
        [sa.text("COALESCE(billed_user_id, user_id)"), "timestamp"],
    )
    op.create_index(
        "ix_generation_jobs_status_available_at_id",
        "generation_jobs",
        ["status", "available_at", "id"],
    )


def downgrade() -> None:
    op.drop_index("ix_generation_jobs_status_available_at_id", table_name="generation_jobs")
    op.drop_index("ix_ai_logs_billing_identity_timestamp", table_name="ai_logs")
    _replace_project_foreign_key(ondelete=None)
