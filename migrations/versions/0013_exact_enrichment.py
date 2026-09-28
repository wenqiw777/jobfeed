"""Persist exact posting identity and bounded enrichment retries."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

from jobfeed.domain.external_identity import external_identity

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("pipeline_runs", sa.Column("scan_progress_json", JSONB()))
    op.add_column("jobs", sa.Column("external_identity", sa.Text()))
    op.add_column("jobs", sa.Column("enrich_attempted_at", sa.DateTime(timezone=True)))
    op.add_column("jobs", sa.Column("enrich_error_code", sa.Text()))
    op.add_column("jobs", sa.Column("enrich_retry_after", sa.DateTime(timezone=True)))
    op.create_index("idx_jobs_external_identity", "jobs", ["external_identity"])
    connection = op.get_bind()
    rows = connection.execute(sa.text("SELECT id,url FROM jobs")).fetchall()
    for job_id, url in rows:
        identity = external_identity(url)
        if identity:
            connection.execute(
                sa.text("UPDATE jobs SET external_identity=:identity WHERE id=:id"),
                {"identity": identity, "id": job_id},
            )
    for platform in ("jobright", "handshake"):
        connection.execute(
            sa.text(
                "UPDATE jobs AS alias SET external_identity=official.external_identity "
                "FROM jobs official WHERE official.platform=:platform "
                "AND alias.external_identity=:platform || ':' || official.canonical_id "
                "AND official.external_identity NOT LIKE :prefix"
            ),
            {"platform": platform, "prefix": platform + ":%"},
        )


def downgrade() -> None:
    op.drop_column("pipeline_runs", "scan_progress_json")
    op.drop_index("idx_jobs_external_identity", table_name="jobs")
    for column in (
        "enrich_retry_after",
        "enrich_error_code",
        "enrich_attempted_at",
        "external_identity",
    ):
        op.drop_column("jobs", column)
