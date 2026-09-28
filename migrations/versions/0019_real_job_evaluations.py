"""Canonical paid evaluation rows separate from source audit rows."""

import sqlalchemy as sa
from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "real_job_evaluations",
        sa.Column(
            "real_job_id",
            sa.BigInteger(),
            sa.ForeignKey("real_jobs.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "source_job_id", sa.Integer(), sa.ForeignKey("jobs.id"), nullable=False
        ),
        sa.Column("input_jd_text", sa.Text(), nullable=False),
        sa.Column("input_facts_json", sa.Text(), nullable=False),
        sa.Column("input_revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("claim_generation", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("stage_a_status", sa.Text()),
        sa.Column("stage_a_error", sa.Text()),
        sa.Column(
            "stage_a_error_count", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("stage_a_score", sa.Integer()),
        sa.Column("stage_a_one_line", sa.Text()),
        sa.Column("stage_a_timing_eligible", sa.Text()),
        sa.Column("stage_a_model", sa.Text()),
        sa.Column("stage_a_cost_usd", sa.Float()),
        sa.Column("stage_a_at", sa.DateTime(timezone=True)),
        sa.Column("stage_b_status", sa.Text()),
        sa.Column("stage_b_error", sa.Text()),
        sa.Column(
            "stage_b_error_count", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("stage_b_verdict", sa.Text()),
        sa.Column("stage_b_json", sa.Text()),
        sa.Column("stage_b_model", sa.Text()),
        sa.Column("stage_b_cost_usd", sa.Float()),
        sa.Column("stage_b_at", sa.DateTime(timezone=True)),
        sa.Column("ml_gate_result", sa.Text()),
        sa.Column("ml_gate_score", sa.Float()),
        sa.Column("ml_gate_json", sa.Text()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "real_job_evaluation_history",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column(
            "real_job_id",
            sa.BigInteger(),
            sa.ForeignKey("real_jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "source_job_id", sa.Integer(), sa.ForeignKey("jobs.id"), nullable=False
        ),
        sa.Column("input_revision", sa.Integer(), nullable=False),
        sa.Column("stage_a_status", sa.Text()),
        sa.Column("stage_a_score", sa.Integer()),
        sa.Column("stage_b_status", sa.Text()),
        sa.Column("stage_b_verdict", sa.Text()),
        sa.Column("stage_b_json", sa.Text()),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("real_job_evaluation_history")
    op.drop_table("real_job_evaluations")
