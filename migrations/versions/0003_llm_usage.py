"""Add llm_usage table for per-call LLM tracking.

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-24
"""

from __future__ import annotations

from alembic import op

revision: str = "0003"
down_revision: str = "0002"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Create the llm_usage table with indexes."""
    op.execute("""
    CREATE TABLE llm_usage (
        id SERIAL PRIMARY KEY,
        model TEXT NOT NULL,
        input_tokens INTEGER NOT NULL,
        output_tokens INTEGER NOT NULL,
        cost_usd REAL NOT NULL DEFAULT 0.0,
        cached BOOLEAN NOT NULL DEFAULT FALSE,
        latency_ms INTEGER NOT NULL DEFAULT 0,
        timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        job_id INTEGER REFERENCES jobs(id),
        stage TEXT,
        run_id TEXT
    )
    """)
    op.execute("CREATE INDEX idx_llm_usage_timestamp ON llm_usage(timestamp)")
    op.execute("CREATE INDEX idx_llm_usage_run ON llm_usage(run_id)")


def downgrade() -> None:
    """Drop the llm_usage table and its indexes."""
    op.execute("DROP INDEX IF EXISTS idx_llm_usage_run")
    op.execute("DROP INDEX IF EXISTS idx_llm_usage_timestamp")
    op.execute("DROP TABLE IF EXISTS llm_usage")
