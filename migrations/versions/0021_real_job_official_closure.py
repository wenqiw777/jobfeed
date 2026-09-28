"""Persist closure verified by official ATS source evidence."""

import sqlalchemy as sa
from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add nullable canonical closure; explicit reconciliation fills old rows."""
    op.add_column(
        "real_jobs", sa.Column("official_closed_at", sa.DateTime(timezone=True))
    )


def downgrade() -> None:
    """Remove only the canonical closure projection."""
    op.drop_column("real_jobs", "official_closed_at")
