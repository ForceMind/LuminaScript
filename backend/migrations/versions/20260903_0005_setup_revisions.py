"""Add monotonic setup revisions without rewriting historical setup values."""
from alembic import op
import sqlalchemy as sa

revision = "20260903_0005"
down_revision = "20260804_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("projects", sa.Column("setup_revision", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("projects", sa.Column("setup_cache_revision", sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    with op.batch_alter_table("projects") as batch_op:
        batch_op.drop_column("setup_cache_revision")
        batch_op.drop_column("setup_revision")
