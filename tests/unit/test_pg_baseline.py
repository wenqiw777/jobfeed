"""Fail-closed and workload contracts for PostgreSQL baseline capture."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from jobfeed.adapters.migration._baseline_evidence import (
    validate_evidence_bundle,
    validate_restore_attestations,
)
from jobfeed.adapters.migration._baseline_machine import machine_fingerprint
from jobfeed.adapters.migration._baseline_workload import (
    REQUIRED_BENCHMARK_COVERAGE,
    validate_benchmark_workload,
)
from jobfeed.adapters.migration._pg_baseline_manifest import aggregate_manifest
from jobfeed.adapters.migration._pg_baseline_reader import _primary_key_order
from jobfeed.adapters.migration._pg_claim_contention import (
    validate_claim_contention_outcome,
)
from jobfeed.adapters.migration.canonical_schema_manifest import (
    canonical_schema_manifest_document,
)
from jobfeed.adapters.migration.pg_baseline import (
    assert_capture_allowed,
    validate_live_schema,
    validate_public_tables,
)
from jobfeed.cli.migrate import _write_new_json, migrate

_WORKLOAD = (
    Path(__file__).resolve().parents[2]
    / "docs"
    / "contracts"
    / "fixtures"
    / "sqlite-store-benchmark-v1.json"
)
_EXPECTED_CLIENTS = 2
_EXPECTED_COROUTINES = 8
_MINIMUM_ROUNDS = 100
_MINIMUM_SAMPLES = 30


def test_frozen_benchmark_workload_covers_every_required_path() -> None:
    """One versioned workload covers hot, view, perf, insights, and DB overhead."""
    document = json.loads(_WORKLOAD.read_text("utf-8"))

    workload = validate_benchmark_workload(document)

    assert {query.coverage for query in workload.operations} == (
        REQUIRED_BENCHMARK_COVERAGE
    )
    assert workload.warmup_count > 0
    assert workload.sample_count > workload.warmup_count
    assert workload.sample_count >= _MINIMUM_SAMPLES
    assert workload.contention.processes == _EXPECTED_CLIENTS
    assert workload.contention.coroutines_per_process == _EXPECTED_COROUTINES
    assert workload.contention.rounds_per_coroutine >= _MINIMUM_ROUNDS
    assert workload.contention.claim_limit == 1
    assert [query.operation for query in workload.operations if query.allow_empty] == [
        "get_step_timings"
    ]


def test_claim_contention_outcome_rejects_duplicates_or_errors() -> None:
    """Contention succeeds only with real unique claims and no worker errors."""
    unique_claims = [str(index) for index in range(_MINIMUM_ROUNDS)]
    validate_claim_contention_outcome(
        claimed_by_process={101: unique_claims[:50], 102: unique_claims[50:]},
        errors=[],
        persisted_claim_ids=unique_claims,
    )

    with pytest.raises(ValueError, match="duplicate"):
        validate_claim_contention_outcome(
            claimed_by_process={101: ["1"], 102: ["1"]},
            errors=[],
            persisted_claim_ids=["1"],
        )
    with pytest.raises(ValueError, match="error"):
        validate_claim_contention_outcome(
            claimed_by_process={101: ["1"], 102: []},
            errors=["boom"],
            persisted_claim_ids=["1"],
        )
    with pytest.raises(ValueError, match="process"):
        validate_claim_contention_outcome(
            claimed_by_process={101: unique_claims, 102: []},
            errors=[],
            persisted_claim_ids=unique_claims,
        )
    with pytest.raises(ValueError, match="ID set"):
        validate_claim_contention_outcome(
            claimed_by_process={101: unique_claims[:50], 102: unique_claims[50:]},
            errors=[],
            persisted_claim_ids=unique_claims[:-1],
        )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("workload_version", 2, "version"),
        ("warmup_count", 0, "warmup"),
        ("sample_count", 0, "sample"),
        ("sample_count", 29, "at least 30"),
    ],
)
def test_invalid_workload_metadata_fails_closed(
    field: str, value: object, message: str
) -> None:
    """Benchmark version and sample controls are exact positive gates."""
    document = json.loads(_WORKLOAD.read_text("utf-8"))
    document[field] = value

    with pytest.raises(ValueError, match=message):
        validate_benchmark_workload(document)


def test_unknown_or_incomplete_benchmark_operation_fails_closed() -> None:
    """The frozen workload accepts only complete typed operation descriptors."""
    document = json.loads(_WORKLOAD.read_text("utf-8"))
    document["operations"][0]["operation"] = "delete_jobs"
    with pytest.raises(ValueError, match="operation"):
        validate_benchmark_workload(document)

    document = json.loads(_WORKLOAD.read_text("utf-8"))
    document["operations"].pop()
    with pytest.raises(ValueError, match="coverage"):
        validate_benchmark_workload(document)

    document = json.loads(_WORKLOAD.read_text("utf-8"))
    document["operations"][0]["allow_empty"] = True
    with pytest.raises(ValueError, match="allow_empty"):
        validate_benchmark_workload(document)


@pytest.mark.parametrize(
    ("revision", "writers", "running", "message"),
    [
        ("0007", 0, 0, "0008"),
        ("0008", 1, 0, "writer"),
        ("0008", 0, 1, "running"),
    ],
)
def test_capture_gate_rejects_unsafe_source_state(
    revision: str, writers: int, running: int, message: str
) -> None:
    """Capture never guesses a revision or finalizes live work."""
    with pytest.raises(ValueError, match=message):
        assert_capture_allowed(
            revision=revision, active_writers=writers, running_runs=running
        )


def test_live_schema_mismatch_fails_closed() -> None:
    """Capture requires exact registry order, PK, type, kind, and nullability."""
    document = canonical_schema_manifest_document()
    validate_live_schema(document)

    changed = copy.deepcopy(document)
    changed["tables"][0]["columns"][0]["source_sql_type"] = "bigint"
    with pytest.raises(ValueError, match="mismatch"):
        validate_live_schema(changed)


def test_live_public_table_set_rejects_extra_or_missing() -> None:
    """The live gate sees tables outside the expected registry enumeration."""
    expected = [
        str(table["name"]) for table in canonical_schema_manifest_document()["tables"]
    ] + ["alembic_version"]
    validate_public_tables(expected)

    with pytest.raises(ValueError, match="extra"):
        validate_public_tables([*expected, "shadow_jobs"])
    with pytest.raises(ValueError, match="missing"):
        validate_public_tables(expected[1:])


def test_text_primary_keys_use_explicit_binary_postgres_order() -> None:
    """Text PK parity is independent of locale for case and Unicode values."""
    assert _primary_key_order("jobs") == '"id"'
    assert _primary_key_order("state") == '"key" COLLATE "C"'
    values = ["é", "Z", "a", "Å"]
    assert sorted(values, key=lambda value: value.encode("utf-8")) == [
        "Z",
        "a",
        "Å",
        "é",
    ]


def test_unordered_aggregate_rows_hash_independently_of_backend_order() -> None:
    """Only explicitly unordered attention buckets are stable-sorted."""
    first = {
        "as_of_utc": "2026-08-12T00:00:00Z",
        "window_days": 30,
        "pending_stage_a": 1,
        "pending_stage_b": 2,
        "needs_attention": {"enrich_errors": [{"job_id": "2"}, {"job_id": "1"}]},
        "funnel": [{"run_id": "b"}, {"run_id": "a"}],
        "daily_cost": [{"day": "2026-08-02"}, {"day": "2026-08-01"}],
        "llm_percentiles": [{"day": "2026-08-02"}, {"day": "2026-08-01"}],
    }
    second = copy.deepcopy(first)
    second["needs_attention"]["enrich_errors"].reverse()

    assert aggregate_manifest(first) == aggregate_manifest(second)
    second["funnel"].reverse()
    assert aggregate_manifest(first) != aggregate_manifest(second)


def test_restore_attestations_and_evidence_bundle_are_exact_and_acyclic(
    tmp_path: Path,
) -> None:
    """Two distinct restores bind one dump; index hashes only prior artifacts."""
    digest = "a" * 64
    base = {
        "attestation_version": 1,
        "dump_sha256": digest,
        "container_id": "container-source",
        "database_identity": "b" * 64,
        "restore_tool": "pg_restore",
        "restore_tool_version": "16.4",
        "restore_command_sha256": "c" * 64,
        "pre_upgrade_revision": "0007",
        "post_upgrade_revision": "0008",
    }
    scratch = {
        **base,
        "container_id": "container-scratch",
        "database_identity": "d" * 64,
    }
    attestations = validate_restore_attestations(base, scratch, dump_sha256=digest)
    manifest = {
        "format_version": 1,
        "created_at_utc": "2026-08-12T00:00:00Z",
        "git_commit": "deadbeef",
        "schema_registry": canonical_schema_manifest_document(),
        "source": {
            "backend": "postgresql",
            "alembic_revision": "0008",
            "source_dump_sha256": digest,
            "source_dump_size_bytes": 1,
            "consistent_snapshot_id": f"pgdump-sha256:{digest}",
            "server_version": "16.4",
            "database_size_bytes": 1,
            "jobs_size_bytes": 1,
        },
        "restore_attestations": attestations,
        "writer_quiescence": {
            "checked_at_utc": "2026-08-12T00:00:00Z",
            "active_jobfeed_writers": 0,
            "historical_running_runs": 0,
        },
        "tables": {
            table["name"]: {
                "row_count": 1 if table["name"] == "jobs" else 0,
                "primary_key": table["primary_key"],
                "max_identity": 1 if table["name"] == "jobs" else None,
                "canonical_sha256": "5" * 64,
            }
            for table in canonical_schema_manifest_document()["tables"]
        },
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
            "as_of_utc": "2026-08-12T00:00:00Z",
            "window_days": 30,
            "pending_stage_a": 0,
            "pending_stage_b": 0,
            "needs_attention_sha256": "6" * 64,
            "funnel_sha256": "7" * 64,
            "daily_cost_sha256": "8" * 64,
            "llm_percentiles_sha256": "9" * 64,
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
    benchmark = {
        "report_version": 1,
        "created_at_utc": "2026-08-12T00:00:00Z",
        "git_commit": "deadbeef",
        "snapshot_manifest_sha256": "e" * 64,
        "workload_sha256": "f" * 64,
        "machine_fingerprint": "2" * 64,
        "machine_token_sha256": "3" * 64,
        "cpu_identifier_sha256": "4" * 64,
        "warmup_count": 1,
        "sample_count": 30,
        "read_consistency": {
            "mode": "quiescent_pre_post_gate_with_fresh_rehash",
            "canonical_manifest": "initial repeatable-read read-only transaction",
            "store_metrics": "separate connections followed by fresh full rehash",
            "contention": "distinct attested disposable scratch restore only",
            "pre_revision": "0008",
            "pre_active_writers": 0,
            "pre_running_runs": 0,
            "post_revision": "0008",
            "post_active_writers": 0,
            "post_running_runs": 0,
        },
        "queries": [
            {
                "name": coverage,
                "coverage": coverage,
                "row_count": 1,
                "p50_ms": 1.0,
                "p95_ms": 1.0,
                "max_ms": 1.0,
            }
            for coverage in sorted(REQUIRED_BENCHMARK_COVERAGE)
        ],
        "contention": {
            "mode": "claim_pending_stage_a",
            "processes": 2,
            "worker_pids": [101, 102],
            "successful_claims_by_process": {"101": 50, "102": 50},
            "coroutines_per_process": 8,
            "rounds_per_coroutine": 100,
            "attempted_short_writes": 1600,
            "successful_claims": 100,
            "database_claim_count": 100,
            "database_claim_ids_sha256": "a" * 64,
            "empty_claims": 1500,
            "duplicate_claims": 0,
            "data_loss": 0,
            "retry_exhausted_busy": 0,
            "scratch_initial_manifest_sha256": "e" * 64,
            "scratch_pre_revision": "0008",
            "scratch_pre_active_writers": 0,
            "scratch_pre_running_runs": 0,
            "scratch_post_revision": "0008",
            "scratch_post_active_writers": 0,
            "scratch_post_running_runs": 0,
            "p50_ms": 1.0,
            "p95_ms": 1.0,
            "max_ms": 1.0,
        },
        "scratch_mutations": {
            "mode": "disposable_scratch_real_writes",
            "setup_in_timed_samples": False,
            "sample_count": 30,
            "scan": {
                "operation": "save_job_insert_then_quality_upgrade",
                "verified_rows": 31,
                "p50_ms": 1.0,
                "p95_ms": 1.0,
                "max_ms": 1.0,
            },
            "evaluate": {
                "operation": "claim_release_result_error",
                "verified_rows": 93,
                "p50_ms": 1.0,
                "p95_ms": 1.0,
                "max_ms": 1.0,
                "paths": {
                    path: {
                        "sample_count": 30,
                        "p50_ms": 1.0,
                        "p95_ms": 1.0,
                        "max_ms": 1.0,
                    }
                    for path in ("claim_release", "claim_result", "claim_error")
                },
            },
        },
    }
    index = {
        "evidence_version": 1,
        "source_dump_sha256": digest,
        "manifest_sha256": "e" * 64,
        "benchmark_sha256": "1" * 64,
        "workload_sha256": "f" * 64,
        "git_commit": "deadbeef",
    }
    validate_evidence_bundle(manifest, benchmark, index, verify_hashes=False)

    manifest_path = tmp_path / "manifest.json"
    _write_new_json(manifest_path, manifest)
    sorted_manifest = json.loads(manifest_path.read_text("utf-8"))
    validate_evidence_bundle(sorted_manifest, benchmark, index, verify_hashes=False)

    malicious = copy.deepcopy(manifest)
    malicious["tables"]["jobs"]["unexpected"] = True
    with pytest.raises(ValueError, match="exact keys"):
        validate_evidence_bundle(malicious, benchmark, index, verify_hashes=False)
    malicious = copy.deepcopy(benchmark)
    malicious["queries"][0]["row_count"] = 0
    with pytest.raises(ValueError, match="row_count"):
        validate_evidence_bundle(manifest, malicious, index, verify_hashes=False)
    malicious = copy.deepcopy(benchmark)
    malicious["contention"]["database_claim_count"] = 99
    with pytest.raises(ValueError, match="claim count"):
        validate_evidence_bundle(manifest, malicious, index, verify_hashes=False)
    malicious = copy.deepcopy(benchmark)
    malicious["contention"]["scratch_initial_manifest_sha256"] = "b" * 64
    with pytest.raises(ValueError, match="scratch manifest"):
        validate_evidence_bundle(manifest, malicious, index, verify_hashes=False)
    malicious = copy.deepcopy(benchmark)
    malicious["contention"]["empty_claims"] = 1499
    with pytest.raises(ValueError, match="attempted"):
        validate_evidence_bundle(manifest, malicious, index, verify_hashes=False)
    malicious = copy.deepcopy(benchmark)
    malicious["queries"][0]["p50_ms"] = 2.0
    with pytest.raises(ValueError, match="timing order"):
        validate_evidence_bundle(manifest, malicious, index, verify_hashes=False)
    malicious = copy.deepcopy(benchmark)
    malicious["read_consistency"]["post_revision"] = "0007"
    with pytest.raises(ValueError, match="read consistency"):
        validate_evidence_bundle(manifest, malicious, index, verify_hashes=False)
    malicious = copy.deepcopy(manifest)
    malicious["activity_maxima"]["jobs"]["unexpected"] = None
    with pytest.raises(ValueError, match="activity maxima"):
        validate_evidence_bundle(malicious, benchmark, index, verify_hashes=False)
    malicious = copy.deepcopy(manifest)
    malicious["tables"]["jobs"]["row_count"] = 0
    with pytest.raises(ValueError, match="non-empty jobs"):
        validate_evidence_bundle(malicious, benchmark, index, verify_hashes=False)
    malicious = copy.deepcopy(manifest)
    malicious["tables"]["jobs"]["max_identity"] = None
    with pytest.raises(ValueError, match="max identity"):
        validate_evidence_bundle(malicious, benchmark, index, verify_hashes=False)

    with pytest.raises(ValueError, match="distinct"):
        validate_restore_attestations(base, base, dump_sha256=digest)
    with pytest.raises(ValueError, match="exact keys"):
        validate_evidence_bundle(
            {**manifest, "benchmark_sha256": "2" * 64},
            benchmark,
            index,
            verify_hashes=False,
        )


def test_machine_fingerprint_hashes_stable_host_and_cpu_without_plaintext() -> None:
    """Hardware comparison binds host and CPU identifiers without exposing them."""
    result = machine_fingerprint("host-uuid-secret", "Apple M4 Max")
    assert result == machine_fingerprint("host-uuid-secret", "Apple M4 Max")
    assert result != machine_fingerprint("other-host", "Apple M4 Max")
    assert "host-uuid-secret" not in result


def test_canonical_bin_forwards_benchmark_environment() -> None:
    """Canonical Docker CLI forwards both scratch DSN and machine token."""
    root = Path(__file__).resolve().parents[2]
    wrapper = (root / "bin" / "jobfeed").read_text("utf-8")
    compose = (root / "docker-compose.yml").read_text("utf-8")

    assert "docker compose run --rm" in wrapper
    scratch_line = (
        'JOBFEED_MIGRATION_SCRATCH_PG_URL: "${JOBFEED_MIGRATION_SCRATCH_PG_URL:-}"'
    )
    assert scratch_line in compose
    assert 'JOBFEED_BENCH_MACHINE_TOKEN: "${JOBFEED_BENCH_MACHINE_TOKEN:-}"' in compose


def test_cli_requires_named_dsn_environment_without_creating_outputs(
    tmp_path: Path,
) -> None:
    """Credentials come only from the named env and missing input writes nothing."""
    artifact_dir = tmp_path / "bundle"
    source_dump = tmp_path / "source.dump"
    source_dump.write_bytes(b"pgdump")
    source_attestation = tmp_path / "source-attestation.json"
    scratch_attestation = tmp_path / "scratch-attestation.json"
    source_attestation.write_text("{}", encoding="utf-8")
    scratch_attestation.write_text("{}", encoding="utf-8")

    result = CliRunner().invoke(
        migrate,
        [
            "benchmark-store",
            "--backend",
            "postgres",
            "--dsn-env",
            "MISSING_BASELINE_DSN",
            "--scratch-dsn-env",
            "MISSING_SCRATCH_DSN",
            "--machine-token-env",
            "MISSING_MACHINE_TOKEN",
            "--workload",
            str(_WORKLOAD),
            "--artifact-dir",
            str(artifact_dir),
            "--source-dump",
            str(source_dump),
            "--source-restore-attestation",
            str(source_attestation),
            "--scratch-restore-attestation",
            str(scratch_attestation),
        ],
        env={
            "MISSING_BASELINE_DSN": "",
            "MISSING_SCRATCH_DSN": "",
            "MISSING_MACHINE_TOKEN": "",
        },
    )

    assert result.exit_code != 0
    assert "No such command 'benchmark-store'" in result.output
    assert not artifact_dir.exists()
