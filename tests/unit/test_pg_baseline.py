"""Fail-closed and workload contracts for PostgreSQL baseline capture."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from jobfeed.adapters.migration._baseline_evidence import (
    machine_fingerprint,
    validate_evidence_bundle,
    validate_restore_attestations,
)
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
from jobfeed.cli.migrate import migrate

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


def test_claim_contention_outcome_rejects_duplicates_or_errors() -> None:
    """Contention succeeds only with real unique claims and no worker errors."""
    unique_claims = [str(index) for index in range(_MINIMUM_ROUNDS)]
    validate_claim_contention_outcome(
        claimed_by_process={101: unique_claims[:50], 102: unique_claims[50:]},
        errors=[],
        database_claim_delta=len(unique_claims),
    )

    with pytest.raises(ValueError, match="duplicate"):
        validate_claim_contention_outcome(
            claimed_by_process={101: ["1"], 102: ["1"]},
            errors=[],
            database_claim_delta=2,
        )
    with pytest.raises(ValueError, match="error"):
        validate_claim_contention_outcome(
            claimed_by_process={101: ["1"], 102: []},
            errors=["boom"],
            database_claim_delta=1,
        )
    with pytest.raises(ValueError, match="process"):
        validate_claim_contention_outcome(
            claimed_by_process={101: unique_claims, 102: []},
            errors=[],
            database_claim_delta=len(unique_claims),
        )
    with pytest.raises(ValueError, match="delta"):
        validate_claim_contention_outcome(
            claimed_by_process={101: unique_claims[:50], 102: unique_claims[50:]},
            errors=[],
            database_claim_delta=len(unique_claims) - 1,
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
    """Golden aggregate hashes sort nested rows by stable serialized keys."""
    first = {
        "pending_stage_a": 1,
        "pending_stage_b": 2,
        "needs_attention": {"enrich_errors": [{"job_id": "2"}, {"job_id": "1"}]},
        "funnel": [{"run_id": "b"}, {"run_id": "a"}],
        "daily_cost": [{"day": "2026-08-02"}, {"day": "2026-08-01"}],
        "llm_percentiles": [{"day": "2026-08-02"}, {"day": "2026-08-01"}],
    }
    second = copy.deepcopy(first)
    second["needs_attention"]["enrich_errors"].reverse()
    second["funnel"].reverse()
    second["daily_cost"].reverse()
    second["llm_percentiles"].reverse()

    assert aggregate_manifest(first) == aggregate_manifest(second)


def test_restore_attestations_and_evidence_bundle_are_exact_and_acyclic() -> None:
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
        "schema_registry": {},
        "source": {"source_dump_sha256": digest},
        "restore_attestations": attestations,
        "writer_quiescence": {},
        "tables": {},
        "activity_maxima": {},
        "aggregates": {},
        "target": {},
    }
    benchmark = {
        "report_version": 1,
        "created_at_utc": "2026-08-12T00:00:00Z",
        "git_commit": "deadbeef",
        "snapshot_manifest_sha256": "e" * 64,
        "workload_sha256": "f" * 64,
        "machine_fingerprint": "2" * 64,
        "host_identifier_sha256": "3" * 64,
        "cpu_identifier_sha256": "4" * 64,
        "warmup_count": 1,
        "sample_count": 30,
        "read_consistency": {},
        "queries": [],
        "contention": {},
        "open_workloads": [],
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
        env={"MISSING_BASELINE_DSN": "", "MISSING_SCRATCH_DSN": ""},
    )

    assert result.exit_code == 1
    assert "environment is empty" in result.output
    assert not artifact_dir.exists()
