"""Phase 9 follow-up: per-run ML-gate survivor counter.

Adds ``pipeline_runs.jobs_gate_passed`` so the performance funnel can report
how many jobs actually passed the ML gate. Previously ``after_gate`` was
derived from the scored counters, which understates gate passes whenever
fewer jobs are scored than survived the gate (Stage A ``limit``, budget
exhaustion, scoring errors). Existing rows backfill to 0; the funnel query
falls back to the scored counters for them.

Revision ID: 0008
Revises: 0007
Create Date: 2026-07-04
"""

from __future__ import annotations

from alembic import op

revision: str = "0008"
down_revision: str = "0007"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Add pipeline_runs.jobs_gate_passed with a 0 default."""
    op.execute(
        "ALTER TABLE pipeline_runs "
        "ADD COLUMN jobs_gate_passed INTEGER NOT NULL DEFAULT 0"
    )


def downgrade() -> None:
    """Remove pipeline_runs.jobs_gate_passed."""
    op.execute("ALTER TABLE pipeline_runs DROP COLUMN jobs_gate_passed")
