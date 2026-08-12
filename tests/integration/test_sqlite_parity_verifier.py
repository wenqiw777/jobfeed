"""Exact PostgreSQL-manifest to SQLite-v1 parity contracts."""

from __future__ import annotations

import copy
from pathlib import Path

import aiosqlite
import pytest

from jobfeed.adapters.migration._baseline_evidence import (
    validate_restore_attestations,
)
from jobfeed.adapters.migration._baseline_workload import artifact_sha256
from jobfeed.adapters.migration._pg_baseline_manifest import aggregate_manifest
from jobfeed.adapters.migration.canonical_row import canonical_rows_sha256
from jobfeed.adapters.migration.canonical_schema_manifest import (
    CANONICAL_ROW_SCHEMAS_V1,
    CANONICAL_SCHEMA_MANIFEST_V1,
    canonical_schema_manifest_document,
)
from jobfeed.adapters.migration.sqlite_parity import (
    SqliteParityVerificationError,
    verify_sqlite_parity,
)
from jobfeed.adapters.store.sqlite_lifecycle import SqliteLifecycle
from jobfeed.adapters.store.sqlite_schema import ensure_sqlite_schema

_AS_OF = "2026-08-12T00:00:00.000000Z"
_EXPECTED_SEEDED_ROWS = 3


async def test_exact_fourteen_table_and_aggregate_parity_returns_typed_report(
    tmp_path: Path,
) -> None:
    """A valid v1 target matches every source count, digest, and aggregate."""
    lifecycle, manifest = await _target_and_manifest(tmp_path)

    report = await verify_sqlite_parity(lifecycle, manifest, chunk_size=1)

    assert report.is_match
    assert report.sqlite_schema_version == 1
    assert report.manifest_sha256 == artifact_sha256(manifest)
    assert tuple(item.table_name for item in report.tables) == tuple(
        table.name for table in CANONICAL_SCHEMA_MANIFEST_V1.tables
    )
    assert sum(item.row_count for item in report.tables) == _EXPECTED_SEEDED_ROWS
    assert report.aggregates.pending_stage_a == 1
    assert report.aggregates.pending_stage_b == 0
    assert report.mismatches == ()
    await lifecycle.close()


@pytest.mark.parametrize(
    ("mutation", "scope"),
    [
        ("missing_table_metric", "manifest"),
        ("extra_table_metric", "manifest"),
        ("source_schema", "manifest"),
        ("row_count", "table"),
        ("row_hash", "table"),
        ("aggregate", "aggregate"),
    ],
)
async def test_manifest_or_data_mismatch_fails_closed_with_typed_report(
    tmp_path: Path,
    mutation: str,
    scope: str,
) -> None:
    """No source coverage, schema, row, hash, or aggregate drift is accepted."""
    lifecycle, manifest = await _target_and_manifest(tmp_path)
    changed = copy.deepcopy(manifest)
    if mutation == "missing_table_metric":
        changed["tables"].pop("state")
    elif mutation == "extra_table_metric":
        changed["tables"]["shadow"] = copy.deepcopy(changed["tables"]["state"])
    elif mutation == "source_schema":
        changed["schema_registry"]["tables"][0]["columns"][0]["target_sqlite_type"] = (
            "TEXT"
        )
    elif mutation == "row_count":
        changed["tables"]["jobs"]["row_count"] += 1
    elif mutation == "row_hash":
        changed["tables"]["jobs"]["canonical_sha256"] = "0" * 64
    else:
        changed["aggregates"]["pending_stage_a"] = 99

    with pytest.raises(SqliteParityVerificationError) as raised:
        await verify_sqlite_parity(lifecycle, changed)

    assert not raised.value.report.is_match
    assert raised.value.report.mismatches[0].scope == scope
    await lifecycle.close()


@pytest.mark.parametrize("mutation", ["extra_table", "changed_column", "lease"])
async def test_target_schema_or_lease_drift_fails_before_row_comparison(
    tmp_path: Path,
    mutation: str,
) -> None:
    """The verifier independently gates the exact target v1 schema and seeds."""
    lifecycle, manifest = await _target_and_manifest(tmp_path)
    async with lifecycle.connection() as connection:
        if mutation == "extra_table":
            await connection.execute("CREATE TABLE shadow(id INTEGER PRIMARY KEY)")
        elif mutation == "changed_column":
            await connection.execute("ALTER TABLE state ADD COLUMN shadow TEXT")
        else:
            await connection.execute(
                "UPDATE run_leases SET generation=1 WHERE kind='scan'"
            )

    with pytest.raises(SqliteParityVerificationError) as raised:
        await verify_sqlite_parity(lifecycle, manifest)

    assert raised.value.report.mismatches[0].scope == "sqlite_schema"
    assert raised.value.report.tables == ()
    await lifecycle.close()


async def test_foreign_key_corruption_fails_closed_even_when_rows_match_manifest(
    tmp_path: Path,
) -> None:
    """Canonical hashes cannot hide referential corruption in the target."""
    lifecycle, _ = await _target_and_manifest(tmp_path)
    async with lifecycle.connection() as connection:
        await connection.execute("PRAGMA foreign_keys=OFF")
        await connection.execute("INSERT INTO evaluations(id, job_id) VALUES(1, 999)")
        await connection.execute("PRAGMA foreign_keys=ON")
    manifest = await _manifest_for(lifecycle)

    with pytest.raises(SqliteParityVerificationError) as raised:
        await verify_sqlite_parity(lifecycle, manifest)

    assert raised.value.report.mismatches[0].scope == "foreign_key"
    await lifecycle.close()


async def _target_and_manifest(
    tmp_path: Path,
) -> tuple[SqliteLifecycle, dict[str, object]]:
    lifecycle = SqliteLifecycle(tmp_path / "target.sqlite", ensure_sqlite_schema)
    await lifecycle.open()
    async with lifecycle.connection() as connection:
        await connection.execute(
            """INSERT INTO jobs(
                   id, platform, canonical_id, url, title, company, location,
                   discovered_at
               ) VALUES(1, 'test', 'one', 'https://example/1', 'Engineer',
                        'Acme', 'NY', '2026-08-01T00:00:00.000000Z')"""
        )
    return lifecycle, await _manifest_for(lifecycle)


async def _manifest_for(lifecycle: SqliteLifecycle) -> dict[str, object]:
    table_metrics: dict[str, object] = {}
    async with lifecycle.connection() as connection:
        connection.row_factory = aiosqlite.Row
        for table, schema in zip(
            CANONICAL_SCHEMA_MANIFEST_V1.tables,
            CANONICAL_ROW_SCHEMAS_V1,
            strict=True,
        ):
            names = ", ".join(f'"{column.name}"' for column in table.columns)
            order = ", ".join(f'"{name}"' for name in table.primary_key)
            cursor = await connection.execute(
                f'SELECT {names} FROM "{table.name}" ORDER BY {order}'
            )
            rows = [dict(row) for row in await cursor.fetchall()]
            await cursor.close()
            table_metrics[table.name] = {
                "row_count": len(rows),
                "primary_key": list(table.primary_key),
                "max_identity": _max_identity(table.name, rows),
                "canonical_sha256": canonical_rows_sha256(schema, rows),
            }
    digest = "a" * 64
    source_attestation = _attestation(digest, "source", "b" * 64)
    scratch_attestation = _attestation(digest, "scratch", "c" * 64)
    return {
        "format_version": 1,
        "created_at_utc": _AS_OF,
        "git_commit": "d" * 40,
        "schema_registry": canonical_schema_manifest_document(),
        "source": {
            "backend": "postgresql",
            "alembic_revision": "0008",
            "server_version": "16.4",
            "database_size_bytes": 1,
            "jobs_size_bytes": 1,
            "consistent_snapshot_id": f"pgdump-sha256:{digest}",
            "source_dump_sha256": digest,
            "source_dump_size_bytes": 1,
        },
        "restore_attestations": validate_restore_attestations(
            source_attestation, scratch_attestation, dump_sha256=digest
        ),
        "writer_quiescence": {
            "checked_at_utc": _AS_OF,
            "active_jobfeed_writers": 0,
            "historical_running_runs": 0,
        },
        "tables": table_metrics,
        "activity_maxima": _activity_maxima(),
        "aggregates": aggregate_manifest(_empty_aggregates()),
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


def _max_identity(table: str, rows: list[dict[str, object]]) -> int | None:
    generated = {
        "jobs",
        "evaluations",
        "pipeline_runs",
        "job_status_history",
        "llm_usage",
        "interview_rounds",
        "step_timings",
    }
    return (
        max((int(row["id"]) for row in rows), default=None)
        if table in generated
        else None
    )


def _attestation(digest: str, suffix: str, identity: str) -> dict[str, object]:
    return {
        "attestation_version": 1,
        "dump_sha256": digest,
        "container_id": f"container-{suffix}",
        "database_identity": identity,
        "restore_tool": "pg_restore",
        "restore_tool_version": "16.4",
        "restore_command_sha256": "e" * 64,
        "pre_upgrade_revision": "0007",
        "post_upgrade_revision": "0008",
    }


def _activity_maxima() -> dict[str, dict[str, object]]:
    return {
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
    }


def _empty_aggregates() -> dict[str, object]:
    return {
        "as_of_utc": _AS_OF,
        "window_days": 30,
        "pending_stage_a": 1,
        "pending_stage_b": 0,
        "needs_attention": {
            "enrich_errors": [],
            "low_quality_scored": [],
            "stuck_scoring": [],
        },
        "funnel": [],
        "daily_cost": [],
        "llm_percentiles": [],
    }
