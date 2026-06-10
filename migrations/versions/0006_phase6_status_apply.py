"""Phase 6: interview_rounds table, status enum consolidation, stale-index refresh.

Creates the ``interview_rounds`` table that tracks per-job interview stages,
backfills rows for the four retired sub-statuses (oa / hr_call / second_round /
final_round), collapses them into ``interviewing``, then swaps the CHECK
constraint and stale index to match the new 11-value status enum.

**Backup**: run ``pg_dump`` before applying this migration.

**Downgrade is LOSSY**: the ``interviewing`` status cannot be mapped back to
its original sub-stage (oa / hr_call / second_round / final_round) because that
information only exists in the ``interview_rounds`` rows which are dropped on
downgrade. Any ``awaiting_referral`` rows are mapped to ``shortlisted``.

Revision ID: 0006
Revises: 0005
Create Date: 2026-06-09
"""

from __future__ import annotations

from alembic import op

revision: str = "0006"
down_revision: str = "0005"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Add interview_rounds, backfill retired statuses, swap constraint."""

    # 1. Create interview_rounds table
    op.execute("""
    CREATE TABLE interview_rounds (
        id SERIAL PRIMARY KEY,
        job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
        round_index INTEGER NOT NULL,
        label TEXT NOT NULL,
        scheduled_at TIMESTAMPTZ,
        completed_at TIMESTAMPTZ,
        notes TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        UNIQUE (job_id, round_index)
    )
    """)
    op.execute("CREATE INDEX idx_interview_rounds_job ON interview_rounds(job_id)")
    op.execute(
        "CREATE INDEX idx_interview_rounds_upcoming "
        "ON interview_rounds(scheduled_at) "
        "WHERE completed_at IS NULL"
    )

    # 2. Backfill retired statuses into interview_rounds, then collapse
    op.execute("""
    INSERT INTO interview_rounds (job_id, round_index, label, completed_at)
    SELECT js.job_id, 1,
        CASE js.status
            WHEN 'oa' THEN 'OA'
            WHEN 'hr_call' THEN 'HR Call'
            WHEN 'second_round' THEN '2nd Round'
            WHEN 'final_round' THEN 'Final Round'
        END,
        now()
    FROM job_status js
    WHERE js.status IN ('oa', 'hr_call', 'second_round', 'final_round')
    AND NOT EXISTS (
        SELECT 1 FROM interview_rounds ir WHERE ir.job_id = js.job_id
    )
    """)
    op.execute("""
    UPDATE job_status
    SET status = 'interviewing'
    WHERE status IN ('oa', 'hr_call', 'second_round', 'final_round')
    """)

    # 3. Swap CHECK constraint (safe now — no retired values remain)
    op.execute("ALTER TABLE job_status DROP CONSTRAINT job_status_status_check")
    op.execute(
        "ALTER TABLE job_status "
        "ADD CONSTRAINT job_status_status_check "
        "CHECK (status IN ("
        "'new', 'scored', 'shortlisted', 'awaiting_referral', "
        "'applied', 'interviewing', 'rejected', 'offer', 'ghosted', "
        "'archived', 'ignored'))"
    )

    # 4. Recreate stale index (old predicate included retired statuses)
    op.execute("DROP INDEX IF EXISTS idx_job_status_stale")
    op.execute(
        "CREATE INDEX idx_job_status_stale "
        "ON job_status(last_status_change_at) "
        "WHERE status IN ('applied', 'interviewing')"
    )


def downgrade() -> None:
    """Reverse Phase 6 status migration (LOSSY — see module docstring)."""

    # Restore old stale index predicate (6 statuses including retired ones)
    op.execute("DROP INDEX IF EXISTS idx_job_status_stale")
    op.execute(
        "CREATE INDEX idx_job_status_stale "
        "ON job_status(last_status_change_at) "
        "WHERE status IN ("
        "'applied', 'interviewing', 'oa', "
        "'hr_call', 'second_round', 'final_round')"
    )

    # Map awaiting_referral back to shortlisted before restoring old CHECK
    op.execute(
        "UPDATE job_status SET status = 'shortlisted' "
        "WHERE status = 'awaiting_referral'"
    )

    # Restore old CHECK constraint (14 statuses)
    op.execute("ALTER TABLE job_status DROP CONSTRAINT job_status_status_check")
    op.execute(
        "ALTER TABLE job_status "
        "ADD CONSTRAINT job_status_status_check "
        "CHECK (status IN ("
        "'new', 'scored', 'shortlisted', 'archived', 'ignored', "
        "'applied', 'interviewing', 'rejected', 'offer', 'ghosted', "
        "'oa', 'hr_call', 'second_round', 'final_round'))"
    )

    # Drop interview_rounds and its indexes
    op.execute("DROP INDEX IF EXISTS idx_interview_rounds_upcoming")
    op.execute("DROP INDEX IF EXISTS idx_interview_rounds_job")
    op.execute("DROP TABLE IF EXISTS interview_rounds")
