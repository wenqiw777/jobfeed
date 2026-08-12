"""Real PostgreSQL-to-SQLite isolated cutover rehearsal evidence."""

from __future__ import annotations

import copy
import sqlite3
from pathlib import Path

import pytest

from jobfeed.adapters.migration._baseline_workload import artifact_sha256
from jobfeed.adapters.migration._pg_baseline_reader import PostgresBaselineReader
from jobfeed.adapters.migration.sqlite_cutover_rehearsal import (
    CutoverSourceEvidence,
    run_cutover_rehearsal,
)
from tests.integration.test_sqlite_forward_import_postgres import _seed_postgres
from tests.unit._sqlite_forward_import_fixture import (
    canonical_source_rows,
    snapshot_manifest,
)


def _source_evidence(dsn: str) -> CutoverSourceEvidence:
    manifest = snapshot_manifest(canonical_source_rows())
    attestations = copy.deepcopy(manifest["restore_attestations"])
    with PostgresBaselineReader(dsn) as reader:
        attestations["source"]["database_identity"] = reader.database_identity()
    return CutoverSourceEvidence(
        git_commit="d" * 40,
        dump_sha256="a" * 64,
        dump_size_bytes=100,
        restore_attestations=attestations,
    )


@pytest.mark.postgres
def test_real_postgres_snapshot_imports_and_proves_exact_sqlite_parity(
    fresh_pg_dsn: str, tmp_path: Path
) -> None:
    """One PG repeatable-read snapshot produces a closed proven SQLite file."""
    _seed_postgres(fresh_pg_dsn, canonical_source_rows())
    target = tmp_path / "jobfeed.sqlite"

    result = run_cutover_rehearsal(
        fresh_pg_dsn,
        destination=target,
        source=_source_evidence(fresh_pg_dsn),
        chunk_size=1,
    )

    assert target.is_file()
    assert not Path(f"{target}-wal").exists()
    assert result.parity_result["is_match"] is True
    assert result.index["manifest_sha256"] == artifact_sha256(result.manifest)
    assert result.index["parity_result_sha256"] == artifact_sha256(result.parity_result)
    connection = sqlite3.connect(target)
    try:
        assert connection.execute("SELECT COUNT(*) FROM jobs").fetchone() == (1,)
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    finally:
        connection.close()


@pytest.mark.postgres
def test_source_identity_failure_publishes_no_sqlite(
    fresh_pg_dsn: str, tmp_path: Path
) -> None:
    """A restore identity mismatch fails before creating the target file."""
    _seed_postgres(fresh_pg_dsn, canonical_source_rows())
    evidence = _source_evidence(fresh_pg_dsn)
    evidence.restore_attestations["source"]["database_identity"] = "0" * 64
    target = tmp_path / "jobfeed.sqlite"

    with pytest.raises(ValueError, match="identity"):
        run_cutover_rehearsal(
            fresh_pg_dsn,
            destination=target,
            source=evidence,
            chunk_size=1,
        )

    assert not target.exists()
