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
from jobfeed.adapters.migration.canonical_schema_manifest import (
    canonical_schema_manifest_document,
)
from jobfeed.adapters.migration.pg_baseline import (
    assert_capture_allowed,
    validate_live_schema,
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


def test_frozen_benchmark_workload_covers_every_required_path() -> None:
    """One versioned workload covers hot, view, perf, insights, and DB overhead."""
    document = json.loads(_WORKLOAD.read_text("utf-8"))

    workload = validate_benchmark_workload(document)

    assert {query.coverage for query in workload.operations} == (
        REQUIRED_BENCHMARK_COVERAGE
    )
    assert workload.warmup_count > 0
    assert workload.sample_count > workload.warmup_count
    assert workload.contention.clients == _EXPECTED_CLIENTS


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


def test_cli_requires_named_dsn_environment_without_creating_outputs(
    tmp_path: Path,
) -> None:
    """Credentials come only from the named env and missing input writes nothing."""
    manifest = tmp_path / "manifest.json"
    benchmark = tmp_path / "benchmark.json"

    result = CliRunner().invoke(
        migrate,
        [
            "capture-pg-baseline",
            "--dsn-env",
            "MISSING_BASELINE_DSN",
            "--workload",
            str(_WORKLOAD),
            "--manifest-output",
            str(manifest),
            "--benchmark-output",
            str(benchmark),
        ],
        env={"MISSING_BASELINE_DSN": ""},
    )

    assert result.exit_code == 1
    assert "MISSING_BASELINE_DSN" in result.output
    assert not manifest.exists()
    assert not benchmark.exists()
