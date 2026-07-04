"""Phase 9: pipeline run status tracking and step-level timing.

Adds a ``status`` column to ``pipeline_runs`` so RunManager can INSERT at
trigger time (status='running') and UPDATE on completion ('succeeded' /
'failed'). Creates the ``step_timings`` table for per-step wall-clock
latency records (observability / performance dashboard).

Revision ID: 0007
Revises: 0006
Create Date: 2026-06-16
"""

from __future__ import annotations

from alembic import op

revision: str = "0007"
down_revision: str = "0006"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Add pipeline_runs.status column and step_timings table."""

    # 1. Add status column to pipeline_runs (nullable first, backfill, then NOT NULL)
    op.execute("ALTER TABLE pipeline_runs ADD COLUMN status TEXT")
    op.execute("UPDATE pipeline_runs SET status = 'succeeded'")
    op.execute("ALTER TABLE pipeline_runs ALTER COLUMN status SET NOT NULL")

    # 2. Create step_timings table
    op.execute("""
    CREATE TABLE step_timings (
        id SERIAL PRIMARY KEY,
        run_id TEXT NOT NULL REFERENCES pipeline_runs(run_id),
        step_type TEXT NOT NULL,
        step_name TEXT NOT NULL,
        elapsed_ms DOUBLE PRECISION NOT NULL,
        is_error BOOLEAN NOT NULL DEFAULT FALSE,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """)
    op.execute("CREATE INDEX idx_step_timings_run ON step_timings(run_id)")
    op.execute(
        "CREATE INDEX idx_step_timings_type_created "
        "ON step_timings(step_type, created_at)"
    )


def downgrade() -> None:
    """Remove step_timings table and pipeline_runs.status column."""
    op.execute("DROP INDEX IF EXISTS idx_step_timings_type_created")
    op.execute("DROP INDEX IF EXISTS idx_step_timings_run")
    op.execute("DROP TABLE IF EXISTS step_timings")
    op.execute("ALTER TABLE pipeline_runs DROP COLUMN status")
