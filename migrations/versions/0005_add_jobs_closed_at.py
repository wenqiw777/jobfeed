"""Add nullable closed_at column to jobs for liveness tracking.

Revision ID: 0005
Revises: 0004
Create Date: 2026-06-04
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str = "0004"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Add jobs.closed_at — nullable, tz-aware timestamp, no default."""
    op.add_column(
        "jobs",
        sa.Column(
            "closed_at",
            sa.TIMESTAMP(timezone=True),
            nullable=True,
        ),
    )


def downgrade() -> None:
    """Drop jobs.closed_at."""
    op.drop_column("jobs", "closed_at")
