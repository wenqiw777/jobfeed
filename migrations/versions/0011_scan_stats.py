"""Persist per-source incoming JD quality statistics for every scan."""

from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE pipeline_runs ADD COLUMN scan_stats_json JSONB")


def downgrade() -> None:
    op.execute("ALTER TABLE pipeline_runs DROP COLUMN scan_stats_json")
