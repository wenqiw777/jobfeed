"""PostgreSQL-0008 to SQLite-v1 forward-import contract tests."""

from __future__ import annotations

import copy
import sqlite3
from pathlib import Path

import pytest

from jobfeed.adapters.migration import _sqlite_forward_db
from jobfeed.adapters.migration.canonical_schema_manifest import (
    MIGRATED_TABLE_ORDER_V1,
)
from jobfeed.adapters.migration.sqlite_forward_import import (
    import_postgres_snapshot_to_sqlite,
)
from tests.unit._sqlite_forward_import_fixture import (
    FakeSnapshotSource,
    canonical_source_rows,
    snapshot_manifest,
    stage_files,
)

_IMPORTED_JOB_ID = 41
_IMPORTED_HISTORY_ID = 71
_DIRECTORY_FSYNC_CALL = 2


def _scalar(connection: sqlite3.Connection, sql: str) -> object:
    row = connection.execute(sql).fetchone()
    assert row is not None
    return row[0]


def test_imports_exact_14_tables_then_installs_trigger_and_preserves_identity(
    tmp_path: Path,
) -> None:
    """A real SQLite file preserves all rows, raw text, UTC, leases, and IDs."""
    rows = canonical_source_rows()
    manifest = snapshot_manifest(rows)
    target = tmp_path / "published.db"

    result = import_postgres_snapshot_to_sqlite(
        FakeSnapshotSource(rows), manifest, target, chunk_size=1
    )

    assert result.path == target
    assert result.row_counts == dict.fromkeys(MIGRATED_TABLE_ORDER_V1, 1)
    assert result.table_sha256 == {
        name: manifest["tables"][name]["canonical_sha256"]
        for name in MIGRATED_TABLE_ORDER_V1
    }
    assert result.sqlite_file_sha256
    connection = sqlite3.connect(target)
    try:
        assert _scalar(connection, "PRAGMA user_version") == 1
        assert _scalar(connection, "PRAGMA integrity_check") == "ok"
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        names = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_schema WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%'"
            )
        }
        assert names == {*MIGRATED_TABLE_ORDER_V1, "run_leases"}
        for table_name in MIGRATED_TABLE_ORDER_V1:
            assert _scalar(connection, f'SELECT COUNT(*) FROM "{table_name}"') == 1
        assert connection.execute(
            "SELECT kind,generation,owner_id,run_id,heartbeat_at,expires_at "
            "FROM run_leases ORDER BY kind"
        ).fetchall() == [
            ("evaluate", 0, None, None, None, None),
            ("scan", 0, None, None, None, None),
        ]
        applied = connection.execute(
            "SELECT verdict_snapshot,fit_snapshot,hooks_snapshot FROM applied"
        ).fetchone()
        assert applied == (' { "b": 2, "a": 1 } ', "raw\ntext", "null")
        assert _scalar(connection, "SELECT discovered_at FROM jobs") == (
            "2026-08-12T13:14:15.123456Z"
        )
        assert _scalar(connection, "SELECT domain_tags FROM jobs") == (
            '{"a":1.0000000000000001e-1,"b":0.0e0}'
        )

        connection.execute(
            "INSERT INTO jobs(platform,canonical_id,url,title,company,location,"
            "discovered_at) VALUES(?,?,?,?,?,?,?)",
            (
                "indeed",
                "next",
                "https://example.test/next",
                "Next",
                "Example",
                "Remote",
                "2026-08-12T14:00:00.000000Z",
            ),
        )
        next_id = int(
            _scalar(connection, "SELECT id FROM jobs WHERE canonical_id='next'")
        )
        assert next_id > _IMPORTED_JOB_ID
        assert (
            _scalar(
                connection, f"SELECT COUNT(*) FROM job_status WHERE job_id={next_id}"
            )
            == 1
        )
        assert (
            _scalar(
                connection,
                f"SELECT COUNT(*) FROM job_status_history WHERE job_id={next_id}",
            )
            == 1
        )
        assert (
            _scalar(connection, "SELECT MAX(id) FROM job_status_history")
            > _IMPORTED_HISTORY_ID
        )
    finally:
        connection.close()


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("revision", "0008"),
        ("schema", "schema"),
        ("extra_table", "public table"),
        ("checksum", "checksum"),
    ],
)
def test_source_contract_mismatch_fails_without_publication(
    tmp_path: Path, mutation: str, message: str
) -> None:
    """Revision, schema, table coverage, and source checksum all fail closed."""
    rows = canonical_source_rows()
    source = FakeSnapshotSource(rows)
    manifest = snapshot_manifest(rows)
    if mutation == "revision":
        source.revision = "0007"
    elif mutation == "schema":
        source.schema["tables"][0]["columns"][0]["nullable"] = True
    elif mutation == "extra_table":
        source.rows["shadow_jobs"] = []
    else:
        manifest["tables"]["jobs"]["canonical_sha256"] = "f" * 64
    target = tmp_path / "must-not-exist.db"

    with pytest.raises(ValueError, match=message):
        import_postgres_snapshot_to_sqlite(source, manifest, target, chunk_size=1)

    assert not target.exists()
    assert stage_files(tmp_path) == []


def test_stream_or_insert_failure_cleans_stage_and_never_touches_existing_target(
    tmp_path: Path,
) -> None:
    """A mid-import exception removes owned files and cannot replace a target."""
    rows = canonical_source_rows()
    source = FakeSnapshotSource(rows)
    source.fail_table = "cost_ledger"
    target = tmp_path / "target.db"

    with pytest.raises(RuntimeError, match="stream failure"):
        import_postgres_snapshot_to_sqlite(
            source, snapshot_manifest(rows), target, chunk_size=1
        )
    assert not target.exists()
    assert stage_files(tmp_path) == []

    target.write_bytes(b"existing production bytes")
    with pytest.raises(FileExistsError):
        import_postgres_snapshot_to_sqlite(
            FakeSnapshotSource(rows), snapshot_manifest(rows), target, chunk_size=1
        )
    assert target.read_bytes() == b"existing production bytes"
    assert stage_files(tmp_path) == []


def test_broken_target_symlink_is_never_followed(tmp_path: Path) -> None:
    """No-replace publication treats a broken symlink as an occupied target."""
    target = tmp_path / "target.db"
    missing = tmp_path / "must-remain-missing.db"
    target.symlink_to(missing)
    rows = canonical_source_rows()

    with pytest.raises(FileExistsError):
        import_postgres_snapshot_to_sqlite(
            FakeSnapshotSource(rows), snapshot_manifest(rows), target
        )

    assert target.is_symlink()
    assert target.readlink() == missing
    assert not missing.exists()


def test_publish_sync_failure_removes_the_owned_target_link(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A durability failure after link creation still leaves no publication."""
    calls = 0
    original_fsync = _sqlite_forward_db.os.fsync

    def fail_directory_sync(descriptor: int) -> None:
        nonlocal calls
        calls += 1
        if calls == _DIRECTORY_FSYNC_CALL:
            raise OSError("injected directory sync failure")
        original_fsync(descriptor)

    monkeypatch.setattr(_sqlite_forward_db.os, "fsync", fail_directory_sync)
    rows = canonical_source_rows()
    target = tmp_path / "target.db"

    with pytest.raises(OSError, match="directory sync failure"):
        import_postgres_snapshot_to_sqlite(
            FakeSnapshotSource(rows), snapshot_manifest(rows), target
        )

    assert not target.exists()
    assert stage_files(tmp_path) == []


def test_manifest_is_validated_before_source_or_target_is_touched(
    tmp_path: Path,
) -> None:
    """Unknown manifest revision and malformed codec registry stop before reads."""
    rows = canonical_source_rows()
    source = FakeSnapshotSource(rows)
    manifest = snapshot_manifest(rows)
    changed = copy.deepcopy(manifest)
    changed["schema_registry"]["canonical_row_codec_version"] = "unknown-v2"

    with pytest.raises(ValueError, match="codec"):
        import_postgres_snapshot_to_sqlite(
            source, changed, tmp_path / "target.db", chunk_size=1
        )

    assert not (tmp_path / "target.db").exists()
    assert stage_files(tmp_path) == []
