"""Add canonical real-job ownership while retaining every source posting."""

import sqlalchemy as sa
from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "real_jobs",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column(
            "representative_job_id", sa.Integer(), sa.ForeignKey("jobs.id"), unique=True
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "identity_review_state", sa.Text(), nullable=False, server_default="clear"
        ),
    )
    op.add_column(
        "jobs", sa.Column("real_job_id", sa.BigInteger(), sa.ForeignKey("real_jobs.id"))
    )
    op.add_column("jobs", sa.Column("apply_url", sa.Text()))
    op.create_index("idx_jobs_real_job_id", "jobs", ["real_job_id"])
    op.create_table(
        "real_job_identifiers",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column(
            "real_job_id",
            sa.BigInteger(),
            sa.ForeignKey("real_jobs.id"),
            nullable=False,
        ),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("scope", sa.Text(), nullable=False),
        sa.Column("native_id", sa.Text(), nullable=False),
        sa.Column(
            "evidence_job_id", sa.Integer(), sa.ForeignKey("jobs.id"), nullable=False
        ),
        sa.Column("observed_url", sa.Text()),
        sa.Column(
            "observed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "provider", "scope", "native_id", name="uq_real_job_identifier"
        ),
    )
    op.create_index(
        "idx_real_job_identifiers_parent", "real_job_identifiers", ["real_job_id"]
    )
    op.create_table(
        "real_job_review_cases",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column(
            "left_real_job_id",
            sa.BigInteger(),
            sa.ForeignKey("real_jobs.id"),
            nullable=False,
        ),
        sa.Column(
            "right_real_job_id",
            sa.BigInteger(),
            sa.ForeignKey("real_jobs.id"),
            nullable=False,
        ),
        sa.Column(
            "left_job_id", sa.Integer(), sa.ForeignKey("jobs.id"), nullable=False
        ),
        sa.Column(
            "right_job_id", sa.Integer(), sa.ForeignKey("jobs.id"), nullable=False
        ),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )


def downgrade() -> None:
    op.drop_table("real_job_review_cases")
    op.drop_index("idx_real_job_identifiers_parent", table_name="real_job_identifiers")
    op.drop_table("real_job_identifiers")
    op.drop_index("idx_jobs_real_job_id", table_name="jobs")
    op.drop_column("jobs", "real_job_id")
    op.drop_column("jobs", "apply_url")
    op.drop_table("real_jobs")
