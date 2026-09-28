"""Preserve explicit repost observations without changing evaluations."""
import sqlalchemy as sa
from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("is_repost", sa.Integer()))
    op.add_column("jobs", sa.Column("repost_evidence", sa.Text()))
    op.add_column("jobs", sa.Column("repost_observed_at", sa.DateTime(timezone=True)))


def downgrade() -> None:
    for column in ("repost_observed_at", "repost_evidence", "is_repost"):
        op.drop_column("jobs", column)
