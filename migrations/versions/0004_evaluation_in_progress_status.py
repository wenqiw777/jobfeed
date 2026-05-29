"""Allow in-progress evaluation claim statuses.

Revision ID: 0004
Revises: 0003
Create Date: 2026-05-25
"""

from __future__ import annotations

from alembic import op

revision: str = "0004"
down_revision: str = "0003"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Expand evaluation status checks for atomic paid-work claims."""
    op.execute("""
    ALTER TABLE evaluations
    DROP CONSTRAINT IF EXISTS evaluations_stage_a_status_check,
    ADD CONSTRAINT evaluations_stage_a_status_check
        CHECK (
            stage_a_status IS NULL
            OR stage_a_status IN ('in_progress', 'completed', 'error')
        )
    """)
    op.execute("""
    ALTER TABLE evaluations
    DROP CONSTRAINT IF EXISTS evaluations_stage_b_status_check,
    ADD CONSTRAINT evaluations_stage_b_status_check
        CHECK (
            stage_b_status IS NULL
            OR stage_b_status IN (
                'in_progress', 'completed', 'error', 'skipped_below_threshold'
            )
        )
    """)


def downgrade() -> None:
    """Restore the previous evaluation status checks."""
    op.execute(
        "UPDATE evaluations SET stage_a_status = 'error' WHERE stage_a_status = 'in_progress'"
    )
    op.execute(
        "UPDATE evaluations SET stage_b_status = 'error' WHERE stage_b_status = 'in_progress'"
    )
    op.execute("""
    ALTER TABLE evaluations
    DROP CONSTRAINT IF EXISTS evaluations_stage_a_status_check,
    ADD CONSTRAINT evaluations_stage_a_status_check
        CHECK (
            stage_a_status IS NULL
            OR stage_a_status IN ('completed', 'error')
        )
    """)
    op.execute("""
    ALTER TABLE evaluations
    DROP CONSTRAINT IF EXISTS evaluations_stage_b_status_check,
    ADD CONSTRAINT evaluations_stage_b_status_check
        CHECK (
            stage_b_status IS NULL
            OR stage_b_status IN ('completed', 'error', 'skipped_below_threshold')
        )
    """)
