"""SQLAlchemy Core metadata for the version-one SQLite store schema."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Final

import sqlalchemy as sa
from sqlalchemy.dialects import sqlite
from sqlalchemy.schema import CreateIndex, CreateTable

from jobfeed.adapters.migration.canonical_schema_manifest import (
    CANONICAL_SCHEMA_MANIFEST_V1,
)

SQLITE_SCHEMA_VERSION: Final = 1
SQLITE_TABLE_NAMES: Final = (
    *(table.name for table in CANONICAL_SCHEMA_MANIFEST_V1.tables),
    "run_leases",
)

_DEFAULTS: Final[dict[tuple[str, str], str]] = {
    ("evaluations", "created_at"): "CURRENT_TIMESTAMP",
    ("evaluations", "updated_at"): "CURRENT_TIMESTAMP",
    ("evaluations", "stage_a_error_count"): "0",
    ("evaluations", "stage_b_error_count"): "0",
    ("pipeline_runs", "jobs_discovered"): "0",
    ("pipeline_runs", "jobs_inserted"): "0",
    ("pipeline_runs", "jobs_updated"): "0",
    ("pipeline_runs", "jobs_filtered"): "0",
    ("pipeline_runs", "jobs_ml_gated"): "0",
    ("pipeline_runs", "stage_a_scored"): "0",
    ("pipeline_runs", "stage_b_scored"): "0",
    ("pipeline_runs", "jobs_scored"): "0",
    ("pipeline_runs", "total_llm_cost_usd"): "0.0",
    ("pipeline_runs", "errors"): "0",
    ("pipeline_runs", "jobs_gate_passed"): "0",
    ("resume_variants", "created_at"): "CURRENT_TIMESTAMP",
    ("job_status", "status"): "'new'",
    ("job_status", "last_status_change_at"): "CURRENT_TIMESTAMP",
    ("job_status_history", "changed_at"): "CURRENT_TIMESTAMP",
    ("applied", "applied_at"): "CURRENT_TIMESTAMP",
    ("resume_snapshots", "captured_at"): "CURRENT_TIMESTAMP",
    ("companies", "ats_override"): "0",
    ("companies", "job_count_last_scan"): "0",
    ("companies", "consecutive_discover_failures"): "0",
    ("cost_ledger", "spent_usd"): "0.0",
    ("cost_ledger", "calls"): "0",
    ("cost_ledger", "last_updated"): "CURRENT_TIMESTAMP",
    ("llm_usage", "cost_usd"): "0.0",
    ("llm_usage", "cached"): "0",
    ("llm_usage", "latency_ms"): "0",
    ("llm_usage", "timestamp"): "CURRENT_TIMESTAMP",
    ("interview_rounds", "created_at"): "CURRENT_TIMESTAMP",
    ("step_timings", "is_error"): "0",
    ("step_timings", "created_at"): "CURRENT_TIMESTAMP",
}
_FOREIGN_KEYS: Final = {
    "evaluations": (("job_id", "jobs", "id", None),),
    "job_status": (
        ("job_id", "jobs", "id", "CASCADE"),
        ("resume_variant", "resume_variants", "name", None),
    ),
    "job_status_history": (("job_id", "jobs", "id", "CASCADE"),),
    "applied": (("job_id", "jobs", "id", "CASCADE"),),
    "llm_usage": (("job_id", "jobs", "id", None),),
    "interview_rounds": (("job_id", "jobs", "id", "CASCADE"),),
    "step_timings": (("run_id", "pipeline_runs", "run_id", None),),
}
_UNIQUE_COLUMNS: Final = {
    "jobs": ("platform", "canonical_id"),
    "evaluations": ("job_id",),
    "pipeline_runs": ("run_id",),
    "interview_rounds": ("job_id", "round_index"),
}


def _column_type(name: str) -> sa.types.TypeEngine[Any]:
    if name == "INTEGER":
        return sa.Integer()
    if name == "TEXT":
        return sa.Text()
    if name == "REAL":
        return sa.REAL()
    raise ValueError(f"unsupported SQLite manifest type: {name}")


def _base_metadata() -> sa.MetaData:
    """Build all manifest tables. Time complexity: O(T * C)."""
    metadata = sa.MetaData()
    for manifest_table in CANONICAL_SCHEMA_MANIFEST_V1.tables:
        columns = []
        for manifest_column in manifest_table.columns:
            default = _DEFAULTS.get((manifest_table.name, manifest_column.name))
            columns.append(
                sa.Column(
                    manifest_column.name,
                    _column_type(manifest_column.target_sqlite_type),
                    primary_key=manifest_column.name in manifest_table.primary_key,
                    nullable=manifest_column.nullable,
                    server_default=sa.text(default) if default else None,
                )
            )
        sa.Table(manifest_table.name, metadata, *columns)
    sa.Table(
        "run_leases",
        metadata,
        sa.Column("kind", sa.Text(), primary_key=True, nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("owner_id", sa.Text()),
        sa.Column("run_id", sa.Text()),
        sa.Column("heartbeat_at", sa.Text()),
        sa.Column("expires_at", sa.Text()),
    )
    return metadata


def _add_relational_constraints(metadata: sa.MetaData) -> None:
    """Attach fixed unique and foreign keys. Time complexity: O(C)."""
    for table_name, columns in _UNIQUE_COLUMNS.items():
        metadata.tables[table_name].append_constraint(sa.UniqueConstraint(*columns))
    for table_name, references in _FOREIGN_KEYS.items():
        table = metadata.tables[table_name]
        for column, target_table, target_column, on_delete in references:
            table.append_constraint(
                sa.ForeignKeyConstraint(
                    [column],
                    [f"{target_table}.{target_column}"],
                    ondelete=on_delete,
                )
            )


def _checks() -> dict[str, tuple[str, ...]]:
    return {
        "jobs": (
            "jd_quality IS NULL OR jd_quality IN "
            "('full','good','partial','stub','missing','abandoned')",
            "ml_gate_score IS NULL OR ml_gate_score BETWEEN 0 AND 1",
            "ml_gate_result IS NULL OR ml_gate_result IN ('pass','fail')",
            "clearance_required IS NULL OR clearance_required IN (0,1)",
            "school_restricted IS NULL OR school_restricted IN (0,1)",
            "is_swe_role IS NULL OR is_swe_role IN (0,1)",
            "domain_tags IS NULL OR json_valid(domain_tags)",
            "tech_required IS NULL OR json_valid(tech_required)",
        ),
        "evaluations": (
            "stage_a_score IS NULL OR stage_a_score BETWEEN 0 AND 100",
            "stage_a_status IS NULL OR stage_a_status IN "
            "('in_progress','completed','error')",
            "stage_b_verdict IS NULL OR stage_b_verdict IN ('apply','consider','skip')",
            "stage_b_status IS NULL OR stage_b_status IN "
            "('in_progress','completed','error','skipped_below_threshold')",
            "stage_b_verdict_json IS NULL OR json_valid(stage_b_verdict_json)",
            "stage_b_summary_json IS NULL OR json_valid(stage_b_summary_json)",
            "stage_b_fit_json IS NULL OR json_valid(stage_b_fit_json)",
            "stage_b_hooks_json IS NULL OR json_valid(stage_b_hooks_json)",
        ),
        "job_status": (
            "status IN ('new','scored','shortlisted','awaiting_referral',"
            "'applied','interviewing','rejected','offer','ghosted','archived','ignored')",
        ),
        "companies": ("ats_override IN (0,1)",),
        "llm_usage": (
            "input_tokens >= 0",
            "output_tokens >= 0",
            "cost_usd >= 0",
            "cached IN (0,1)",
            "latency_ms >= 0",
            "stage IS NULL OR stage IN ('a','b')",
        ),
        "step_timings": ("is_error IN (0,1)",),
        "run_leases": (
            "kind IN ('scan','evaluate')",
            "generation >= 0",
            "((owner_id IS NULL AND run_id IS NULL AND heartbeat_at IS NULL "
            "AND expires_at IS NULL) OR (owner_id IS NOT NULL AND run_id IS NOT NULL "
            "AND heartbeat_at IS NOT NULL AND expires_at IS NOT NULL))",
        ),
    }


def _add_checks(metadata: sa.MetaData) -> None:
    """Attach every fixed check constraint. Time complexity: O(C)."""
    for table_name, expressions in _checks().items():
        for position, expression in enumerate(expressions, start=1):
            metadata.tables[table_name].append_constraint(
                sa.CheckConstraint(
                    expression,
                    name=f"ck_{table_name}_{position}",
                )
            )


def _index(
    name: str,
    columns: Iterable[sa.ColumnElement[Any]],
    *,
    where: str | None = None,
) -> None:
    sa.Index(
        name,
        *columns,
        sqlite_where=sa.text(where) if where else None,
    )


def _add_indexes(metadata: sa.MetaData) -> None:
    tables = metadata.tables
    _index(
        "idx_jobs_dedup_softkey",
        (tables["jobs"].c.company_norm, tables["jobs"].c.title_norm),
    )
    _index(
        "idx_jobs_discovered_at",
        (tables["jobs"].c.discovered_at.desc(),),
    )
    _index(
        "idx_companies_vendor",
        (tables["companies"].c.ats_vendor,),
        where="ats_vendor IS NOT NULL",
    )
    _index(
        "idx_eval_stage_a_score",
        (tables["evaluations"].c.stage_a_score.desc(),),
        where="stage_a_status = 'completed'",
    )
    _index(
        "idx_eval_stage_b_queue",
        (tables["evaluations"].c.job_id,),
        where=(
            "stage_a_status = 'completed' AND "
            "(stage_b_status IS NULL OR stage_b_status = 'error')"
        ),
    )
    _index(
        "idx_eval_stage_b_completed",
        (tables["evaluations"].c.stage_a_score,),
        where="stage_b_status = 'completed'",
    )
    _index(
        "idx_job_status_status",
        (tables["job_status"].c.status,),
    )
    _index(
        "idx_job_status_followup",
        (tables["job_status"].c.next_followup_at,),
        where="next_followup_at IS NOT NULL",
    )
    _index(
        "idx_job_status_stale",
        (tables["job_status"].c.last_status_change_at,),
        where="status IN ('applied','interviewing')",
    )
    _index(
        "idx_job_status_history_job",
        (
            tables["job_status_history"].c.job_id,
            tables["job_status_history"].c.changed_at.desc(),
        ),
    )
    _index(
        "idx_jsh_applied_at",
        (tables["job_status_history"].c.changed_at,),
        where="to_status = 'applied'",
    )
    _index(
        "idx_llm_usage_timestamp",
        (tables["llm_usage"].c.timestamp,),
    )
    _index("idx_llm_usage_run", (tables["llm_usage"].c.run_id,))
    _index(
        "idx_interview_rounds_job",
        (tables["interview_rounds"].c.job_id,),
    )
    _index(
        "idx_interview_rounds_upcoming",
        (tables["interview_rounds"].c.scheduled_at,),
        where="completed_at IS NULL",
    )
    _index(
        "idx_step_timings_run",
        (tables["step_timings"].c.run_id,),
    )
    _index(
        "idx_step_timings_type_created",
        (tables["step_timings"].c.step_type, tables["step_timings"].c.created_at),
    )


SQLITE_METADATA: Final = _base_metadata()
_add_relational_constraints(SQLITE_METADATA)
_add_checks(SQLITE_METADATA)
_add_indexes(SQLITE_METADATA)

SQLITE_TRIGGER_SQL: Final = """
CREATE TRIGGER trg_jobs_seed_status
AFTER INSERT ON jobs
FOR EACH ROW
BEGIN
    INSERT OR IGNORE INTO job_status (job_id, status) VALUES (NEW.id, 'new');
    INSERT INTO job_status_history (job_id, from_status, to_status)
        VALUES (NEW.id, NULL, 'new');
END
""".strip()


def schema_ddl_statements() -> tuple[str, ...]:
    """Return ordered transactional v1 DDL statements.

    Returns:
        Table, index, and trigger DDL with no implicit transaction commands.
    """
    dialect = sqlite.dialect()
    tables = tuple(
        str(CreateTable(table).compile(dialect=dialect))
        for table in SQLITE_METADATA.sorted_tables
    )
    indexes = tuple(
        str(CreateIndex(index).compile(dialect=dialect))
        for table in SQLITE_METADATA.sorted_tables
        for index in sorted(table.indexes, key=lambda item: item.name or "")
    )
    return (*tables, *indexes, SQLITE_TRIGGER_SQL)
