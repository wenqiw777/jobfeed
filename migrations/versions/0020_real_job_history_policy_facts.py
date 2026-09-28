"""Retain readable policy facts when archiving canonical evaluations."""

import sqlalchemy as sa
from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add nullable facts without rewriting earlier history rows."""
    op.add_column(
        "real_job_evaluation_history",
        sa.Column("input_facts_json", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    """Return to 0019, discarding only the added policy-facts column."""
    op.drop_column("real_job_evaluation_history", "input_facts_json")
