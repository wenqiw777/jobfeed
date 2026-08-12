"""Canonical all-table fixture for forward-import tests."""

from __future__ import annotations

import copy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jobfeed.adapters.migration.canonical_row import canonical_rows_sha256
from jobfeed.adapters.migration.canonical_schema_manifest import (
    CANONICAL_ROW_SCHEMAS_V1,
    CANONICAL_SCHEMA_MANIFEST_V1,
    canonical_schema_manifest_document,
)

_GENERATED_IDS = {
    "jobs",
    "evaluations",
    "pipeline_runs",
    "job_status_history",
    "llm_usage",
    "interview_rounds",
    "step_timings",
}
_TIME = datetime(2026, 8, 12, 13, 14, 15, 123456, tzinfo=UTC)


def _default(kind: str, nullable: bool, label: str) -> object:
    if nullable:
        return None
    return {
        "bool": True,
        "int": 1,
        "float": 1.25,
        "timestamp": _TIME,
        "text": f"{label}-雪",
        "json": '{"b":-0.0,"a":0.10000000000000001}',
    }[kind]


def canonical_source_rows() -> dict[str, list[dict[str, object]]]:
    """Return one FK-consistent, codec-rich row for every migrated table."""
    rows: dict[str, list[dict[str, object]]] = {}
    for table in CANONICAL_SCHEMA_MANIFEST_V1.tables:
        row = {
            column.name: _default(
                column.codec_kind,
                column.nullable,
                f"{table.name}-{column.name}",
            )
            for column in table.columns
        }
        rows[table.name] = [row]

    overrides: dict[str, dict[str, object]] = {
        "jobs": {
            "id": 41,
            "platform": "indeed",
            "canonical_id": "canonical-雪",
            "url": "https://example.test/job/41",
            "title": "Engineer 雪",
            "company": "Example",
            "location": "Remote",
            "discovered_at": _TIME,
            "jd_quality": "full",
            "domain_tags": '{"b":-0.0,"a":0.10000000000000001}',
            "tech_required": '["Python","C++"]',
            "clearance_required": False,
            "school_restricted": True,
            "is_swe_role": True,
            "ml_gate_score": 0.75,
            "ml_gate_result": "pass",
        },
        "evaluations": {
            "id": 51,
            "job_id": 41,
            "stage_b_fit_json": '{"score":0.10000000000000001,"zero":-0.0}',
        },
        "pipeline_runs": {
            "id": 61,
            "run_id": "run-雪",
            "source": "evaluate",
            "status": "completed",
            "jobs_gate_passed": 1,
        },
        "resume_variants": {"name": "resume-main"},
        "job_status": {
            "job_id": 41,
            "status": "applied",
            "resume_variant": "resume-main",
        },
        "job_status_history": {
            "id": 71,
            "job_id": 41,
            "to_status": "applied",
            "resume_variant_at_change": "resume-main",
        },
        "applied": {
            "job_id": 41,
            "verdict_snapshot": ' { "b": 2, "a": 1 } ',
            "fit_snapshot": "raw\ntext",
            "hooks_snapshot": "null",
        },
        "resume_snapshots": {"resume_hash": "resume-hash", "source": "master"},
        "companies": {"slug": "example", "ats_override": True},
        "cost_ledger": {"day": "2026-08-12"},
        "state": {"key": "unicode-雪", "value": "value-雪"},
        "llm_usage": {"id": 81, "job_id": 41, "run_id": "run-雪"},
        "interview_rounds": {"id": 91, "job_id": 41},
        "step_timings": {"id": 101, "run_id": "run-雪", "is_error": True},
    }
    for table, values in overrides.items():
        rows[table][0].update(values)
    return rows


def snapshot_manifest(rows: dict[str, list[dict[str, object]]]) -> dict[str, object]:
    """Build an exact baseline manifest bound to the supplied row fixture."""
    digest = "a" * 64
    attestation = {
        "attestation_version": 1,
        "dump_sha256": digest,
        "container_id": "source-container",
        "database_identity": "b" * 64,
        "restore_tool": "pg_restore",
        "restore_tool_version": "16.13",
        "restore_command_sha256": "c" * 64,
        "pre_upgrade_revision": "0007",
        "post_upgrade_revision": "0008",
    }
    metrics: dict[str, object] = {}
    for table, schema in zip(
        CANONICAL_SCHEMA_MANIFEST_V1.tables,
        CANONICAL_ROW_SCHEMAS_V1,
        strict=True,
    ):
        table_rows = rows[table.name]
        metrics[table.name] = {
            "row_count": len(table_rows),
            "primary_key": list(table.primary_key),
            "max_identity": (
                max(int(row["id"]) for row in table_rows)
                if table.name in _GENERATED_IDS and table_rows
                else None
            ),
            "canonical_sha256": canonical_rows_sha256(schema, table_rows),
        }
    return {
        "format_version": 1,
        "created_at_utc": "2026-08-12T13:14:15.123456Z",
        "git_commit": "d" * 40,
        "schema_registry": canonical_schema_manifest_document(),
        "source": {
            "backend": "postgresql",
            "alembic_revision": "0008",
            "source_dump_sha256": digest,
            "source_dump_size_bytes": 1,
            "consistent_snapshot_id": f"pgdump-sha256:{digest}",
            "server_version": "16.13",
            "database_size_bytes": 1,
            "jobs_size_bytes": 1,
        },
        "restore_attestations": {
            "source": attestation,
            "scratch": {
                **attestation,
                "container_id": "scratch-container",
                "database_identity": "e" * 64,
            },
        },
        "writer_quiescence": {
            "checked_at_utc": "2026-08-12T13:14:15.123456Z",
            "active_jobfeed_writers": 0,
            "historical_running_runs": 0,
        },
        "tables": metrics,
        "activity_maxima": {
            "jobs": {"discovered_at": None, "enriched_at": None, "closed_at": None},
            "pipeline_runs": {"started_at": None, "finished_at": None},
            "llm_usage": {"timestamp": None},
            "step_timings": {"created_at": None},
            "applied": {"applied_at": None},
            "job_status_history": {"changed_at": None},
            "interview_rounds": {
                "created_at": None,
                "scheduled_at": None,
                "completed_at": None,
            },
        },
        "aggregates": {
            "as_of_utc": "2026-08-12T13:14:15.123456Z",
            "window_days": 30,
            "pending_stage_a": 0,
            "pending_stage_b": 0,
            "needs_attention_sha256": "1" * 64,
            "funnel_sha256": "2" * 64,
            "daily_cost_sha256": "3" * 64,
            "llm_percentiles_sha256": "4" * 64,
        },
        "target": {
            "status": "not_applicable_postgres_baseline",
            "backend": "sqlite",
            "sqlite_schema_version": 1,
            "minimum_sqlite_version": "3.35.0",
            "migrated_table_count": 14,
            "total_table_count": 15,
            "sqlite_file_sha256": None,
        },
    }


class FakeSnapshotSource:
    """Open-snapshot-shaped source with deterministic failure injection."""

    def __init__(self, rows: dict[str, list[dict[str, object]]]) -> None:
        self.rows = copy.deepcopy(rows)
        self.revision = "0008"
        self.schema = canonical_schema_manifest_document()
        self.fail_table: str | None = None

    def scalar(self, sql: str) -> object:
        assert sql == "SELECT version_num FROM alembic_version"
        return self.revision

    def live_schema_document(self) -> dict[str, object]:
        return copy.deepcopy(self.schema)

    def public_base_tables(self) -> list[str]:
        return [*self.rows, "alembic_version"]

    def stream_table(self, table_name: str, chunk_size: int) -> Any:
        assert chunk_size > 0
        if table_name == self.fail_table:
            raise RuntimeError("injected PostgreSQL stream failure")
        yield from copy.deepcopy(self.rows[table_name])


def stage_files(parent: Path) -> list[Path]:
    """List importer-owned temporary database and sidecar files."""
    return list(parent.glob(".*.forward-import-*.tmp*"))
