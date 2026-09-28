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
    "job_priority_snapshot",
    "real_jobs",
    "real_job_identifiers",
    "real_job_review_cases",
    "real_job_status",
    "real_job_status_history",
    "real_job_interview_rounds",
    "real_job_applications",
    "real_job_evaluations",
    "real_job_evaluation_history",
)
_UTC_TIMESTAMP_SQL: Final = "strftime('%Y-%m-%dT%H:%M:%f000Z','now')"

_DEFAULTS: Final[dict[tuple[str, str], str]] = {
    ("evaluations", "created_at"): _UTC_TIMESTAMP_SQL,
    ("evaluations", "updated_at"): _UTC_TIMESTAMP_SQL,
    ("evaluations", "stage_a_error_count"): "0",
    ("evaluations", "stage_b_error_count"): "0",
    ("pipeline_runs", "jobs_discovered"): "0",
    ("pipeline_runs", "jobs_inserted"): "0",
    ("pipeline_runs", "jobs_updated"): "0",
    ("pipeline_runs", "jobs_filtered"): "0",
    ("pipeline_runs", "jobs_ml_gated"): "0",
    ("pipeline_runs", "jobs_seniority_filtered"): "0",
    ("pipeline_runs", "stage_a_scored"): "0",
    ("pipeline_runs", "stage_b_scored"): "0",
    ("pipeline_runs", "jobs_scored"): "0",
    ("pipeline_runs", "total_llm_cost_usd"): "0.0",
    ("pipeline_runs", "errors"): "0",
    ("pipeline_runs", "jobs_gate_passed"): "0",
    ("pipeline_runs", "restart_count"): "0",
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
    sa.Table(
        "job_priority_snapshot",
        metadata,
        sa.Column("job_id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("eligibility_status", sa.Text(), nullable=False),
        sa.Column("eligibility_reason", sa.Text()),
        sa.Column("eligibility_evidence", sa.Text()),
        sa.Column("enrollment_eligibility", sa.Text(), nullable=False),
        sa.Column("in_scope", sa.Integer(), nullable=False),
        sa.Column("display_representative", sa.Integer(), nullable=False),
        sa.Column("blocked_rank", sa.Integer(), nullable=False),
        sa.Column("queue_tier", sa.Integer(), nullable=False),
        sa.Column("pending_rank", sa.Integer(), nullable=False),
        sa.Column("priority_score", sa.REAL(), nullable=False),
        sa.Column("compensation_annual_midpoint", sa.Integer()),
        sa.Column("compensation_percentile", sa.REAL()),
        sa.Column("compensation_score", sa.REAL(), nullable=False),
        sa.Column("company_strength_score", sa.REAL(), nullable=False),
        sa.Column("freshness_score", sa.REAL(), nullable=False),
        sa.Column("new_grad_clarity_score", sa.REAL(), nullable=False),
        sa.Column("evidence_fit_score", sa.REAL()),
        sa.Column("role_direction_score", sa.REAL(), nullable=False),
        sa.Column("posted_sort_at", sa.Text(), nullable=False),
        sa.Column("discovered_sort_at", sa.Text(), nullable=False),
        sa.Column("policy_version", sa.Text(), nullable=False),
        sa.Column("priority_input_updated_at", sa.Text(), nullable=False),
        sa.Column("input_fingerprint", sa.Text(), nullable=False),
        sa.Column("computed_at", sa.Text(), nullable=False),
    )
    metadata.tables["jobs"].append_column(sa.Column("real_job_id", sa.Integer()))
    metadata.tables["jobs"].append_column(sa.Column("apply_url", sa.Text()))
    sa.Table(
        "real_jobs",
        metadata,
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("representative_job_id", sa.Integer()),
        sa.Column("official_closed_at", sa.Text()),
        sa.Column("first_discovered_at", sa.Text()),
        sa.Column("canonical_posted_at", sa.Text()),
        sa.Column(
            "created_at",
            sa.Text(),
            nullable=False,
            server_default=sa.text(_UTC_TIMESTAMP_SQL),
        ),
        sa.Column(
            "updated_at",
            sa.Text(),
            nullable=False,
            server_default=sa.text(_UTC_TIMESTAMP_SQL),
        ),
        sa.Column(
            "identity_review_state",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'clear'"),
        ),
    )
    sa.Table(
        "real_job_identifiers",
        metadata,
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("real_job_id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("scope", sa.Text(), nullable=False),
        sa.Column("native_id", sa.Text(), nullable=False),
        sa.Column("evidence_job_id", sa.Integer(), nullable=False),
        sa.Column("observed_url", sa.Text()),
        sa.Column(
            "observed_at",
            sa.Text(),
            nullable=False,
            server_default=sa.text(_UTC_TIMESTAMP_SQL),
        ),
    )
    sa.Table(
        "real_job_review_cases",
        metadata,
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("left_real_job_id", sa.Integer(), nullable=False),
        sa.Column("right_real_job_id", sa.Integer(), nullable=False),
        sa.Column("left_job_id", sa.Integer(), nullable=False),
        sa.Column("right_job_id", sa.Integer(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.Text(),
            nullable=False,
            server_default=sa.text(_UTC_TIMESTAMP_SQL),
        ),
    )
    sa.Table(
        "real_job_status",
        metadata,
        sa.Column("real_job_id", sa.Integer(), primary_key=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="new"),
        sa.Column("next_followup_at", sa.Text()),
        sa.Column("resume_variant", sa.Text()),
        sa.Column("notes", sa.Text()),
        sa.Column(
            "last_status_change_at",
            sa.Text(),
            nullable=False,
            server_default=sa.text(_UTC_TIMESTAMP_SQL),
        ),
    )
    sa.Table(
        "real_job_status_history",
        metadata,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("real_job_id", sa.Integer(), nullable=False),
        sa.Column("source_history_id", sa.Integer(), unique=True),
        sa.Column("from_status", sa.Text()),
        sa.Column("to_status", sa.Text(), nullable=False),
        sa.Column(
            "changed_at",
            sa.Text(),
            nullable=False,
            server_default=sa.text(_UTC_TIMESTAMP_SQL),
        ),
        sa.Column("reason", sa.Text()),
        sa.Column("resume_variant_at_change", sa.Text()),
    )
    sa.Table(
        "real_job_interview_rounds",
        metadata,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("real_job_id", sa.Integer(), nullable=False),
        sa.Column("source_round_id", sa.Integer(), unique=True),
        sa.Column("round_index", sa.Integer(), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("scheduled_at", sa.Text()),
        sa.Column("completed_at", sa.Text()),
        sa.Column("notes", sa.Text()),
        sa.Column(
            "created_at",
            sa.Text(),
            nullable=False,
            server_default=sa.text(_UTC_TIMESTAMP_SQL),
        ),
        sa.UniqueConstraint("real_job_id", "round_index"),
    )
    sa.Table(
        "real_job_applications",
        metadata,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("real_job_id", sa.Integer(), nullable=False),
        sa.Column("source_job_id", sa.Integer()),
        sa.Column("source_applied_job_id", sa.Integer(), unique=True),
        sa.Column("apply_url", sa.Text()),
        sa.Column("applied_at", sa.Text(), nullable=False),
        sa.Column("notes", sa.Text()),
        sa.Column("cover_letter", sa.Text()),
        sa.Column("application_method", sa.Text()),
        sa.Column("verdict_snapshot", sa.Text()),
        sa.Column("fit_snapshot", sa.Text()),
        sa.Column("hooks_snapshot", sa.Text()),
    )
    sa.Table(
        "real_job_evaluations",
        metadata,
        sa.Column("real_job_id", sa.Integer(), primary_key=True),
        sa.Column("source_job_id", sa.Integer(), nullable=False),
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
        sa.Column("stage_a_cost_usd", sa.REAL()),
        sa.Column("stage_a_at", sa.Text()),
        sa.Column("stage_b_status", sa.Text()),
        sa.Column("stage_b_error", sa.Text()),
        sa.Column(
            "stage_b_error_count", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("stage_b_verdict", sa.Text()),
        sa.Column("stage_b_json", sa.Text()),
        sa.Column("stage_b_model", sa.Text()),
        sa.Column("stage_b_cost_usd", sa.REAL()),
        sa.Column("stage_b_at", sa.Text()),
        sa.Column("ml_gate_result", sa.Text()),
        sa.Column("ml_gate_score", sa.REAL()),
        sa.Column("ml_gate_json", sa.Text()),
        sa.Column("updated_at", sa.Text(), nullable=False),
    )
    sa.Table(
        "real_job_evaluation_history",
        metadata,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("real_job_id", sa.Integer(), nullable=False),
        sa.Column("source_job_id", sa.Integer(), nullable=False),
        sa.Column("input_revision", sa.Integer(), nullable=False),
        sa.Column("stage_a_status", sa.Text()),
        sa.Column("stage_a_score", sa.Integer()),
        sa.Column("stage_b_status", sa.Text()),
        sa.Column("stage_b_verdict", sa.Text()),
        sa.Column("stage_b_json", sa.Text()),
        sa.Column("archived_at", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("input_facts_json", sa.Text()),
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
    metadata.tables["jobs"].append_constraint(
        sa.ForeignKeyConstraint(["real_job_id"], ["real_jobs.id"])
    )
    metadata.tables["real_job_applications"].append_constraint(
        sa.ForeignKeyConstraint(["source_job_id"], ["jobs.id"])
    )
    metadata.tables["real_job_evaluations"].append_constraint(
        sa.ForeignKeyConstraint(["source_job_id"], ["jobs.id"])
    )
    metadata.tables["real_job_evaluation_history"].append_constraint(
        sa.ForeignKeyConstraint(["source_job_id"], ["jobs.id"])
    )
    metadata.tables["real_job_applications"].append_constraint(
        sa.ForeignKeyConstraint(["source_applied_job_id"], ["applied.job_id"])
    )
    metadata.tables["real_jobs"].append_constraint(
        sa.UniqueConstraint("representative_job_id")
    )
    metadata.tables["real_job_identifiers"].append_constraint(
        sa.UniqueConstraint("provider", "scope", "native_id")
    )
    for table_name, columns in {
        "real_job_identifiers": ("real_job_id", "evidence_job_id"),
        "real_job_review_cases": (
            "left_real_job_id",
            "right_real_job_id",
            "left_job_id",
            "right_job_id",
        ),
    }.items():
        for column in columns:
            target = "real_jobs" if "real_job" in column else "jobs"
            metadata.tables[table_name].append_constraint(
                sa.ForeignKeyConstraint([column], [f"{target}.id"])
            )
    for name in (
        "real_job_status",
        "real_job_status_history",
        "real_job_interview_rounds",
        "real_job_applications",
        "real_job_evaluations",
        "real_job_evaluation_history",
    ):
        metadata.tables[name].append_constraint(
            sa.ForeignKeyConstraint(
                ["real_job_id"],
                ["real_jobs.id"],
                ondelete="CASCADE",
            )
        )
    metadata.tables["real_job_status"].append_constraint(
        sa.ForeignKeyConstraint(
            ["resume_variant"],
            ["resume_variants.name"],
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
        "job_priority_snapshot": (
            "eligibility_status IN ('pending','apply','blocked')",
            "enrollment_eligibility IN ('eligible','uncertain','not_applicable')",
            "in_scope IN (0,1)",
            "display_representative IN (0,1)",
            "blocked_rank IN (0,1)",
            "pending_rank IN (0,1)",
            "queue_tier >= 0",
            "priority_score BETWEEN 0 AND 100",
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
    _index("idx_real_job_status_status", (tables["real_job_status"].c.status,))
    _index(
        "idx_real_job_status_history_job",
        (
            tables["real_job_status_history"].c.real_job_id,
            tables["real_job_status_history"].c.changed_at.desc(),
        ),
    )
    _index(
        "idx_real_job_interviews_job",
        (tables["real_job_interview_rounds"].c.real_job_id,),
    )
    _index(
        "idx_real_job_applications_parent",
        (
            tables["real_job_applications"].c.real_job_id,
            tables["real_job_applications"].c.applied_at,
        ),
    )
    _index("idx_jobs_real_job_id", (tables["jobs"].c.real_job_id,))
    _index(
        "idx_real_job_identifiers_parent",
        (tables["real_job_identifiers"].c.real_job_id,),
    )
    _index("idx_jobs_external_identity", (tables["jobs"].c.external_identity,))
    _index(
        "idx_jobs_personal_ml",
        (
            tables["jobs"].c.id,
            tables["jobs"].c.ml_gate_score,
            tables["jobs"].c.ml_gate_fail_reason,
            tables["jobs"].c.role_type,
        ),
    )
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
        "idx_eval_personal_ml",
        (
            tables["evaluations"].c.stage_a_at,
            tables["evaluations"].c.job_id,
            tables["evaluations"].c.stage_a_score,
        ),
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
        "idx_eval_verdict_job",
        (tables["evaluations"].c.job_id,),
        where="stage_b_verdict IS NOT NULL",
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
    _index(
        "idx_job_priority_snapshot_page",
        (
            tables["job_priority_snapshot"].c.in_scope,
            tables["job_priority_snapshot"].c.display_representative,
            tables["job_priority_snapshot"].c.blocked_rank,
            tables["job_priority_snapshot"].c.queue_tier,
            tables["job_priority_snapshot"].c.pending_rank,
            tables["job_priority_snapshot"].c.priority_score.desc(),
            tables["job_priority_snapshot"].c.posted_sort_at.desc(),
            tables["job_priority_snapshot"].c.job_id.desc(),
        ),
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
