"""Add performance indexes for Stage B queue, digest, and application stats.

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-23
"""

from alembic import op

revision: str = "0002"
down_revision: str = "0001"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Add three partial/covering indexes for hot query paths."""
    # Widen Stage B queue index to include error-retry rows (the pending query
    # selects stage_b_status IS NULL OR stage_b_status = 'error').
    op.drop_index("idx_eval_stage_b_queue", table_name="evaluations")
    op.create_index(
        "idx_eval_stage_b_queue",
        "evaluations",
        ["job_id"],
        postgresql_where=(
            "stage_a_status = 'completed'"
            " AND (stage_b_status IS NULL OR stage_b_status = 'error')"
        ),
    )

    # Covering index for digest_stats and top_evaluated_jobs, which filter on
    # stage_b_status = 'completed' and ORDER BY stage_a_score DESC.
    op.create_index(
        "idx_eval_stage_b_completed",
        "evaluations",
        ["stage_a_score"],
        postgresql_where="stage_b_status = 'completed'",
    )

    # Partial index for application_stats applied-in-window lookups.
    op.create_index(
        "idx_jsh_applied_at",
        "job_status_history",
        ["changed_at"],
        postgresql_where="to_status = 'applied'",
    )


def downgrade() -> None:
    """Revert to the original narrow Stage B queue index."""
    op.drop_index("idx_jsh_applied_at", table_name="job_status_history")
    op.drop_index("idx_eval_stage_b_completed", table_name="evaluations")
    op.drop_index("idx_eval_stage_b_queue", table_name="evaluations")
    op.create_index(
        "idx_eval_stage_b_queue",
        "evaluations",
        ["job_id"],
        postgresql_where=("stage_a_status = 'completed' AND stage_b_status IS NULL"),
    )
