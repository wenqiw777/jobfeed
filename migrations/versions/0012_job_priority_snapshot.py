"""Add the disposable precomputed Triage priority snapshot.

Revision ID: 0012
Revises: 0011
"""

from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE TABLE job_priority_snapshot (
        job_id INTEGER PRIMARY KEY,
        eligibility_status TEXT NOT NULL CHECK (eligibility_status IN ('pending','apply','blocked')),
        eligibility_reason TEXT,
        eligibility_evidence TEXT,
        enrollment_eligibility TEXT NOT NULL CHECK (
            enrollment_eligibility IN ('eligible','uncertain','not_applicable')
        ),
        in_scope INTEGER NOT NULL CHECK (in_scope IN (0,1)),
        display_representative INTEGER NOT NULL CHECK (display_representative IN (0,1)),
        blocked_rank INTEGER NOT NULL CHECK (blocked_rank IN (0,1)),
        queue_tier INTEGER NOT NULL CHECK (queue_tier >= 0),
        pending_rank INTEGER NOT NULL CHECK (pending_rank IN (0,1)),
        priority_score DOUBLE PRECISION NOT NULL CHECK (priority_score BETWEEN 0 AND 100),
        compensation_annual_midpoint INTEGER,
        compensation_percentile DOUBLE PRECISION,
        compensation_score DOUBLE PRECISION NOT NULL,
        company_strength_score DOUBLE PRECISION NOT NULL,
        freshness_score DOUBLE PRECISION NOT NULL,
        new_grad_clarity_score DOUBLE PRECISION NOT NULL,
        evidence_fit_score DOUBLE PRECISION,
        role_direction_score DOUBLE PRECISION NOT NULL,
        posted_sort_at TIMESTAMPTZ NOT NULL,
        discovered_sort_at TIMESTAMPTZ NOT NULL,
        policy_version TEXT NOT NULL,
        priority_input_updated_at TIMESTAMPTZ NOT NULL,
        input_fingerprint TEXT NOT NULL,
        computed_at TIMESTAMPTZ NOT NULL
    )
    """)
    op.execute("""
    CREATE INDEX idx_job_priority_snapshot_page ON job_priority_snapshot (
        in_scope, display_representative, blocked_rank, queue_tier, pending_rank,
        priority_score DESC, posted_sort_at DESC, job_id DESC
    )
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS job_priority_snapshot")
