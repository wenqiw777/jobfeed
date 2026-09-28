"""Preserve source application audits as canonical submission events."""

import sqlalchemy as sa
from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "real_job_applications",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column(
            "real_job_id",
            sa.BigInteger(),
            sa.ForeignKey("real_jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source_job_id", sa.Integer(), sa.ForeignKey("jobs.id")),
        sa.Column(
            "source_applied_job_id",
            sa.Integer(),
            sa.ForeignKey("applied.job_id"),
            unique=True,
        ),
        sa.Column("apply_url", sa.Text()),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("notes", sa.Text()),
        sa.Column("cover_letter", sa.Text()),
        sa.Column("application_method", sa.Text()),
        sa.Column("verdict_snapshot", sa.Text()),
        sa.Column("fit_snapshot", sa.Text()),
        sa.Column("hooks_snapshot", sa.Text()),
    )
    op.create_index(
        "idx_real_job_applications_parent",
        "real_job_applications",
        ["real_job_id", "applied_at"],
    )
    op.execute(
        "INSERT INTO real_job_applications("
        "real_job_id,source_job_id,source_applied_job_id,apply_url,applied_at,"
        "notes,cover_letter,"
        "application_method,verdict_snapshot,fit_snapshot,hooks_snapshot) "
        "SELECT j.real_job_id,a.job_id,a.job_id,NULL,a.applied_at,a.notes,"
        "a.cover_letter,"
        "a.application_method,a.verdict_snapshot,a.fit_snapshot,a.hooks_snapshot "
        "FROM applied a JOIN jobs j ON j.id=a.job_id "
        "WHERE j.real_job_id IS NOT NULL ON CONFLICT(source_applied_job_id) DO NOTHING"
    )


def downgrade() -> None:
    op.drop_index(
        "idx_real_job_applications_parent", table_name="real_job_applications"
    )
    op.drop_table("real_job_applications")
