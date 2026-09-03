"""Persist run failure details, checkpoints, and restart linkage."""

from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE pipeline_runs ADD COLUMN failure_code TEXT")
    op.execute("ALTER TABLE pipeline_runs ADD COLUMN failure_message TEXT")
    op.execute("ALTER TABLE pipeline_runs ADD COLUMN failed_stage TEXT")
    op.execute("ALTER TABLE pipeline_runs ADD COLUMN failed_source TEXT")
    op.execute(
        "ALTER TABLE pipeline_runs ADD COLUMN last_progress_at TIMESTAMP WITH TIME ZONE"
    )
    op.execute(
        "ALTER TABLE pipeline_runs ADD COLUMN restart_count INTEGER NOT NULL DEFAULT 0"
    )
    op.execute("ALTER TABLE pipeline_runs ADD COLUMN restarted_by_run_id TEXT")


def downgrade() -> None:
    op.execute("ALTER TABLE pipeline_runs DROP COLUMN restarted_by_run_id")
    op.execute("ALTER TABLE pipeline_runs DROP COLUMN restart_count")
    op.execute("ALTER TABLE pipeline_runs DROP COLUMN last_progress_at")
    op.execute("ALTER TABLE pipeline_runs DROP COLUMN failed_source")
    op.execute("ALTER TABLE pipeline_runs DROP COLUMN failed_stage")
    op.execute("ALTER TABLE pipeline_runs DROP COLUMN failure_message")
    op.execute("ALTER TABLE pipeline_runs DROP COLUMN failure_code")
