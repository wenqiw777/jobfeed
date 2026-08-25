"""SQLAlchemy Core metadata for the version-two SQLite store schema."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Final

import sqlalchemy as sa
from sqlalchemy.dialects import sqlite
from sqlalchemy.schema import CreateIndex, CreateTable

from jobfeed.adapters.migration.canonical_schema_manifest import (
    CANONICAL_SCHEMA_MANIFEST_V1,
)

SQLITE_SCHEMA_VERSION: Final = 2
SQLITE_TABLE_NAMES: Final = (
    *(table.name for table in CANONICAL_SCHEMA_MANIFEST_V1.tables),
    "run_leases",
    "evaluation_results",
)
_UTC_TIMESTAMP_SQL: Final = "strftime('%Y-%m-%dT%H:%M:%f000Z','now')"

_DEFAULTS: Final[dict[tuple[str, str], str]] = {
    ("evaluations", "created_at"): _UTC_TIMESTAMP_SQL,
    ("evaluations", "updated_at"): _UTC_TIMESTAMP_SQL,
    ("evaluations", "stage_a_error_count"): "0",
    ("evaluations", "stage_b_error_count"): "0",
    ("evaluation_results", "updated_at"): _UTC_TIMESTAMP_SQL,
    ("evaluation_results", "error_count"): "0",
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
    ("resume_variants", "created_at"): _UTC_TIMESTAMP_SQL,
    ("job_status", "status"): "'new'",
    ("job_status", "last_status_change_at"): _UTC_TIMESTAMP_SQL,
    ("job_status_history", "changed_at"): _UTC_TIMESTAMP_SQL,
    ("applied", "applied_at"): _UTC_TIMESTAMP_SQL,
    ("resume_snapshots", "captured_at"): _UTC_TIMESTAMP_SQL,
    ("companies", "ats_override"): "0",
    ("companies", "job_count_last_scan"): "0",
    ("companies", "consecutive_discover_failures"): "0",
    ("cost_ledger", "spent_usd"): "0.0",
    ("cost_ledger", "calls"): "0",
    ("cost_ledger", "last_updated"): _UTC_TIMESTAMP_SQL,
    ("llm_usage", "cost_usd"): "0.0",
    ("llm_usage", "cached"): "0",
    ("llm_usage", "latency_ms"): "0",
    ("llm_usage", "timestamp"): _UTC_TIMESTAMP_SQL,
    ("interview_rounds", "created_at"): _UTC_TIMESTAMP_SQL,
    ("step_timings", "is_error"): "0",
    ("step_timings", "created_at"): _UTC_TIMESTAMP_SQL,
}
_FOREIGN_KEYS: Final = {
    "evaluations": (("job_id", "jobs", "id", None),),
    "evaluation_results": (("job_id", "jobs", "id", "CASCADE"),),
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


def _base_metadata(*, include_evaluation_results: bool) -> sa.MetaData:
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
    if include_evaluation_results:
        sa.Table(
            "evaluation_results",
            metadata,
            sa.Column("job_id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("status", sa.Text(), nullable=False),
            sa.Column("eligibility_status", sa.Text()),
            sa.Column("match_tier", sa.Text()),
            sa.Column("match_score", sa.Integer()),
            sa.Column("ats_visibility_score", sa.Integer()),
            sa.Column("result_json", sa.Text()),
            sa.Column("evaluator_version", sa.Text(), nullable=False),
            sa.Column("model", sa.Text()),
            sa.Column("prompt_hash", sa.Text()),
            sa.Column("resume_hash", sa.Text()),
            sa.Column("cost_usd", sa.REAL()),
            sa.Column("evaluated_at", sa.Text()),
            sa.Column(
                "updated_at",
                sa.Text(),
                nullable=False,
                server_default=sa.text(_UTC_TIMESTAMP_SQL),
            ),
            sa.Column("error", sa.Text()),
            sa.Column(
                "error_count",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("0"),
            ),
        )
    return metadata


def _add_relational_constraints(metadata: sa.MetaData) -> None:
    """Attach fixed unique and foreign keys. Time complexity: O(C)."""
    for table_name, columns in _UNIQUE_COLUMNS.items():
        if table_name not in metadata.tables:
            continue
        metadata.tables[table_name].append_constraint(sa.UniqueConstraint(*columns))
    for table_name, references in _FOREIGN_KEYS.items():
        if table_name not in metadata.tables:
            continue
        table = metadata.tables[table_name]
        for column, target_table, target_column, on_delete in references:
            table.append_constraint(
                sa.ForeignKeyConstraint(
                    [column],
                    [f"{target_table}.{target_column}"],
                    ondelete=on_delete,
                )
            )


def _checks(*, include_evaluation_stage: bool) -> dict[str, tuple[str, ...]]:
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
        "evaluation_results": (
            "status IN ('in_progress','completed','error')",
            "eligibility_status IS NULL OR eligibility_status IN "
            "('pass','fail','unclear')",
            "match_tier IS NULL OR match_tier IN "
            "('strong_match','possible_match','weak_match','ineligible')",
            "match_score IS NULL OR match_score BETWEEN 0 AND 100",
            "ats_visibility_score IS NULL OR ats_visibility_score BETWEEN 0 AND 100",
            "result_json IS NULL OR json_valid(result_json)",
            "cost_usd IS NULL OR cost_usd >= 0",
            "error_count >= 0",
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
            "stage IS NULL OR stage IN "
            + ("('a','b','evaluation')" if include_evaluation_stage else "('a','b')"),
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


def _add_checks(metadata: sa.MetaData, *, include_evaluation_stage: bool) -> None:
    """Attach every fixed check constraint. Time complexity: O(C)."""
    for table_name, expressions in _checks(
        include_evaluation_stage=include_evaluation_stage
    ).items():
        if table_name not in metadata.tables:
            continue
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
    if "evaluation_results" in tables:
        _index(
            "idx_evaluation_results_queue",
            (
                tables["evaluation_results"].c.status,
                tables["evaluation_results"].c.evaluator_version,
                tables["evaluation_results"].c.updated_at,
            ),
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


_SQLITE_METADATA_V1: Final = _base_metadata(include_evaluation_results=False)
_add_relational_constraints(_SQLITE_METADATA_V1)
_add_checks(_SQLITE_METADATA_V1, include_evaluation_stage=False)
_add_indexes(_SQLITE_METADATA_V1)

SQLITE_METADATA: Final = _base_metadata(include_evaluation_results=True)
_add_relational_constraints(SQLITE_METADATA)
_add_checks(SQLITE_METADATA, include_evaluation_stage=True)
_add_indexes(SQLITE_METADATA)

SQLITE_TRIGGER_SQL: Final = """
CREATE TRIGGER trg_jobs_seed_status
AFTER INSERT ON jobs
FOR EACH ROW
BEGIN
    INSERT OR IGNORE INTO job_status (job_id, status, last_status_change_at)
        VALUES (NEW.id, 'new', strftime('%Y-%m-%dT%H:%M:%f000Z','now'));
    INSERT INTO job_status_history (job_id, from_status, to_status, changed_at)
        VALUES (
            NEW.id,
            NULL,
            'new',
            strftime('%Y-%m-%dT%H:%M:%f000Z','now')
        );
END
""".strip()


def _ddl_statements(metadata: sa.MetaData) -> tuple[str, ...]:
    """Compile ordered transactional DDL for one metadata snapshot."""
    dialect = sqlite.dialect()
    tables = tuple(
        str(CreateTable(table).compile(dialect=dialect))
        for table in metadata.sorted_tables
    )
    indexes = tuple(
        str(CreateIndex(index).compile(dialect=dialect))
        for table in metadata.sorted_tables
        for index in sorted(table.indexes, key=lambda item: item.name or "")
    )
    return (*tables, *indexes, SQLITE_TRIGGER_SQL)


def schema_ddl_statements() -> tuple[str, ...]:
    """Return ordered transactional v2 DDL statements.

    Returns:
        Table, index, and trigger DDL with no implicit transaction commands.
    """
    return _ddl_statements(SQLITE_METADATA)


def schema_v1_ddl_statements() -> tuple[str, ...]:
    """Return the frozen v1 DDL used to validate and migrate existing stores."""
    return _ddl_statements(_SQLITE_METADATA_V1)


def schema_v2_migration_statements() -> tuple[str, ...]:
    """Return only additive DDL needed to migrate an exact v1 store to v2."""
    dialect = sqlite.dialect()
    table = SQLITE_METADATA.tables["evaluation_results"]
    indexes = tuple(
        str(CreateIndex(index).compile(dialect=dialect))
        for index in sorted(table.indexes, key=lambda item: item.name or "")
    )
    return (str(CreateTable(table).compile(dialect=dialect)), *indexes)


def llm_usage_v2_rebuild_statements() -> tuple[str, ...]:
    """Return the transactional table rebuild that admits evaluation usage rows."""
    dialect = sqlite.dialect()
    table = SQLITE_METADATA.tables["llm_usage"]
    columns = ", ".join(column.name for column in table.columns)
    indexes = tuple(
        str(CreateIndex(index).compile(dialect=dialect))
        for index in sorted(table.indexes, key=lambda item: item.name or "")
    )
    return (
        "ALTER TABLE llm_usage RENAME TO llm_usage_v1",
        str(CreateTable(table).compile(dialect=dialect)),
        f"INSERT INTO llm_usage ({columns}) SELECT {columns} FROM llm_usage_v1",
        "DROP TABLE llm_usage_v1",
        *indexes,
    )
