"""Separate unconfirmed quick setup working drafts from analysis cache."""
from alembic import op
import sqlalchemy as sa

revision = "20260903_0006"
down_revision = "20260903_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("projects", sa.Column("quick_setup_draft", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("projects") as batch_op:
        batch_op.drop_column("quick_setup_draft")
