"""Separate AI operator from billed user without reattributing historical logs."""
from alembic import op
import sqlalchemy as sa

revision = "20260904_0007"
down_revision = "20260903_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("ai_logs") as batch:
        batch.add_column(sa.Column("billed_user_id", sa.Integer(), nullable=True))
        batch.create_foreign_key("fk_ai_logs_billed_user_id_users", "users", ["billed_user_id"], ["id"])
        batch.create_index("ix_ai_logs_billed_user_id", ["billed_user_id"])


def downgrade() -> None:
    with op.batch_alter_table("ai_logs") as batch:
        batch.drop_index("ix_ai_logs_billed_user_id")
        batch.drop_constraint("fk_ai_logs_billed_user_id_users", type_="foreignkey")
        batch.drop_column("billed_user_id")
