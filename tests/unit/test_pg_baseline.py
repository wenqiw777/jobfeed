"""Fail-closed and workload contracts for PostgreSQL baseline capture."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from jobfeed.adapters.migration._baseline_workload import (
    REQUIRED_BENCHMARK_COVERAGE,
    validate_benchmark_workload,
)
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
        worker_pids=[101, 102], claimed_ids=unique_claims, errors=[]
    )

    with pytest.raises(ValueError, match="duplicate"):
        validate_claim_contention_outcome(
            worker_pids=[101, 102], claimed_ids=["1", "1"], errors=[]
        )
    with pytest.raises(ValueError, match="error"):
        validate_claim_contention_outcome(
            worker_pids=[101, 102], claimed_ids=["1"], errors=["boom"]
        )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("workload_version", 2, "version"),
        ("warmup_count", 0, "warmup"),
        ("sample_count", 0, "sample"),
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


def test_cli_requires_named_dsn_environment_without_creating_outputs(
    tmp_path: Path,
) -> None:
    """Credentials come only from the named env and missing input writes nothing."""
    artifact_dir = tmp_path / "bundle"
    source_dump = tmp_path / "source.dump"
    source_dump.write_bytes(b"pgdump")

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
        ],
        env={"MISSING_BASELINE_DSN": "", "MISSING_SCRATCH_DSN": ""},
    )

    assert result.exit_code == 1
    assert "environment is empty" in result.output
    assert not artifact_dir.exists()
