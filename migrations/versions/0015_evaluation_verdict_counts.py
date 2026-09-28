"""Persist per-run Stage B recommendation counts."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("pipeline_runs", sa.Column("verdict_counts_json", JSONB()))


def downgrade() -> None:
    op.drop_column("pipeline_runs", "verdict_counts_json")
