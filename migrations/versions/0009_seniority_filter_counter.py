"""Add the seniority-filter counter to pipeline runs.

Revision ID: 0009_seniority_filter_counter
Revises: 0008_gate_passed_counter
"""

from alembic import op

revision = "0009_seniority_filter_counter"
down_revision = "0008_gate_passed_counter"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Persist seniority exclusions separately from job-rule exclusions."""
    op.execute(
        "ALTER TABLE pipeline_runs "
        "ADD COLUMN jobs_seniority_filtered INTEGER NOT NULL DEFAULT 0"
    )


def downgrade() -> None:
    """Remove the seniority-filter counter."""
    op.execute("ALTER TABLE pipeline_runs DROP COLUMN jobs_seniority_filtered")
