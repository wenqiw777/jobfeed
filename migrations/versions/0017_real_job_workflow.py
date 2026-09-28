"""Add canonical workflow projections while preserving source workflow audit."""

import sqlalchemy as sa
from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "real_job_status",
        sa.Column(
            "real_job_id",
            sa.BigInteger(),
            sa.ForeignKey("real_jobs.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("status", sa.Text(), nullable=False, server_default="new"),
        sa.Column("next_followup_at", sa.DateTime(timezone=True)),
        sa.Column("resume_variant", sa.Text(), sa.ForeignKey("resume_variants.name")),
        sa.Column("notes", sa.Text()),
        sa.Column(
            "last_status_change_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("idx_real_job_status_status", "real_job_status", ["status"])
    op.create_table(
        "real_job_status_history",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column(
            "real_job_id",
            sa.BigInteger(),
            sa.ForeignKey("real_jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "source_history_id",
            sa.Integer(),
            sa.ForeignKey("job_status_history.id"),
            unique=True,
        ),
        sa.Column("from_status", sa.Text()),
        sa.Column("to_status", sa.Text(), nullable=False),
        sa.Column(
            "changed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("reason", sa.Text()),
        sa.Column("resume_variant_at_change", sa.Text()),
    )
    op.create_index(
        "idx_real_job_status_history_job",
        "real_job_status_history",
        ["real_job_id", "changed_at"],
    )
    op.create_table(
        "real_job_interview_rounds",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column(
            "real_job_id",
            sa.BigInteger(),
            sa.ForeignKey("real_jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "source_round_id",
            sa.Integer(),
            sa.ForeignKey("interview_rounds.id"),
            unique=True,
        ),
        sa.Column("round_index", sa.Integer(), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("scheduled_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("notes", sa.Text()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("real_job_id", "round_index"),
    )
    op.create_index(
        "idx_real_job_interviews_job", "real_job_interview_rounds", ["real_job_id"]
    )


def downgrade() -> None:
    op.drop_index("idx_real_job_interviews_job", table_name="real_job_interview_rounds")
    op.drop_table("real_job_interview_rounds")
    op.drop_index(
        "idx_real_job_status_history_job", table_name="real_job_status_history"
    )
    op.drop_table("real_job_status_history")
    op.drop_index("idx_real_job_status_status", table_name="real_job_status")
    op.drop_table("real_job_status")
