"""Safe publication contracts for imported SQLite migration artifacts."""

from __future__ import annotations

import os
import sqlite3
from hashlib import sha256
from pathlib import Path

import pytest

from jobfeed.adapters.migration import _sqlite_artifact_files
from jobfeed.adapters.migration.sqlite_artifact import (
    MigrationArtifactStateError,
    MigrationArtifactValidationError,
    SourceManifestMetadata,
    SqliteMigrationArtifact,
)
from jobfeed.adapters.store._sqlite_errors import SqliteLifecycleBusyError
from jobfeed.adapters.store._sqlite_lock import DatabaseFileLock


def _source_metadata() -> SourceManifestMetadata:
    return SourceManifestMetadata(
        manifest_sha256="a" * 64,
        format_version=1,
        source_backend="postgresql",
        source_schema_revision="0008",
        canonical_row_codec_version="jobfeed-canonical-row-v1",
    )


def _write_valid_database(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("CREATE TABLE parent(id INTEGER PRIMARY KEY)")
        connection.execute(
            "CREATE TABLE child(parent_id INTEGER REFERENCES parent(id))"
        )
        connection.execute("INSERT INTO parent(id) VALUES (1)")
        connection.execute("INSERT INTO child(parent_id) VALUES (1)")


def _workspace_paths(parent: Path, target_name: str) -> list[Path]:
    return list(parent.glob(f".{target_name}.migration-*"))


def test_source_manifest_metadata_fails_closed_on_unusable_identity() -> None:
    """Publisher metadata rejects malformed hashes and missing source identity."""
    with pytest.raises(ValueError, match="SHA-256"):
        SourceManifestMetadata(
            manifest_sha256="A" * 64,
            format_version=1,
            source_backend="postgresql",
            source_schema_revision="0008",
            canonical_row_codec_version="jobfeed-canonical-row-v1",
        )
    with pytest.raises(ValueError, match="format version"):
        SourceManifestMetadata(
            manifest_sha256="a" * 64,
            format_version=0,
            source_backend="postgresql",
            source_schema_revision="0008",
            canonical_row_codec_version="jobfeed-canonical-row-v1",
        )
    with pytest.raises(ValueError, match="source backend"):
        SourceManifestMetadata(
            manifest_sha256="a" * 64,
            format_version=1,
            source_backend="",
            source_schema_revision="0008",
            canonical_row_codec_version="jobfeed-canonical-row-v1",
        )


async def test_valid_artifact_publishes_exclusively_with_bound_metadata(
    tmp_path: Path,
) -> None:
    """A validated staged database becomes one durable final file."""
    target = tmp_path / "jobfeed.next.sqlite"
    source = _source_metadata()
    seen: list[tuple[Path, SourceManifestMetadata]] = []

    async def verify(path: Path, metadata: SourceManifestMetadata) -> None:
        seen.append((path, metadata))
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as connection:
            assert connection.execute("SELECT COUNT(*) FROM child").fetchone() == (1,)

    with SqliteMigrationArtifact.create(target, source) as artifact:
        assert artifact.staging_path.parent.parent == target.parent
        assert artifact.staging_path.name == "artifact.sqlite"
        assert artifact.staging_path.stat().st_mode & 0o777 == 0o600
        assert artifact.staging_path.parent.stat().st_mode & 0o777 == 0o700
        _write_valid_database(artifact.staging_path)
        result = await artifact.publish(verify)

    assert result.path == target
    assert result.source_manifest == source
    assert result.sqlite_sha256 == sha256(target.read_bytes()).hexdigest()
    assert len(result.sqlite_sha256) == 64
    assert result.size_bytes == target.stat().st_size
    assert len(seen) == 1
    assert seen[0][1] == source
    assert seen[0][0].name == "artifact.sqlite"
    assert target.stat().st_mode & 0o777 == 0o600
    assert _workspace_paths(tmp_path, target.name) == []
    with sqlite3.connect(target) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)


async def test_corruption_and_validator_failure_remove_only_run_owned_stage(
    tmp_path: Path,
) -> None:
    """Invalid bytes or parity failure publish nothing and clean the workspace."""
    target = tmp_path / "failed.sqlite"
    called = False

    async def should_not_run(
        _path: Path,
        _metadata: SourceManifestMetadata,
    ) -> None:
        nonlocal called
        called = True

    with pytest.raises(MigrationArtifactValidationError, match="validation failed"):
        with SqliteMigrationArtifact.create(target, _source_metadata()) as artifact:
            artifact.staging_path.write_bytes(b"not sqlite")
            await artifact.publish(should_not_run)
    assert not called
    assert not target.exists()
    assert _workspace_paths(tmp_path, target.name) == []

    async def reject_parity(
        _path: Path,
        _metadata: SourceManifestMetadata,
    ) -> None:
        raise ValueError("checksum mismatch")

    with pytest.raises(ValueError, match="checksum mismatch"):
        with SqliteMigrationArtifact.create(target, _source_metadata()) as artifact:
            _write_valid_database(artifact.staging_path)
            await artifact.publish(reject_parity)
    assert not target.exists()
    assert _workspace_paths(tmp_path, target.name) == []


async def test_concurrent_destination_is_never_replaced(tmp_path: Path) -> None:
    """A target created during verification wins and remains untouched."""
    target = tmp_path / "race.sqlite"

    async def competing_publish(
        _path: Path,
        _metadata: SourceManifestMetadata,
    ) -> None:
        target.write_bytes(b"other-run")

    with pytest.raises(FileExistsError):
        with SqliteMigrationArtifact.create(target, _source_metadata()) as artifact:
            _write_valid_database(artifact.staging_path)
            await artifact.publish(competing_publish)

    assert target.read_bytes() == b"other-run"
    assert _workspace_paths(tmp_path, target.name) == []


def test_existing_target_symlink_or_active_lifecycle_lock_is_refused(
    tmp_path: Path,
) -> None:
    """Publication never replaces existing objects or an actively locked path."""
    target = tmp_path / "existing.sqlite"
    target.write_bytes(b"production")
    with pytest.raises(FileExistsError):
        SqliteMigrationArtifact.create(target, _source_metadata())
    assert target.read_bytes() == b"production"

    symlink_target = tmp_path / "symlink.sqlite"
    symlink_target.symlink_to(target)
    with pytest.raises(FileExistsError):
        SqliteMigrationArtifact.create(symlink_target, _source_metadata())
    assert symlink_target.is_symlink()
    assert target.read_bytes() == b"production"

    absent = tmp_path / "active.sqlite"
    active_lock = DatabaseFileLock(absent)
    active_lock.acquire_shared()
    try:
        with pytest.raises(SqliteLifecycleBusyError):
            SqliteMigrationArtifact.create(absent, _source_metadata())
    finally:
        active_lock.release()
    assert not absent.exists()
    assert _workspace_paths(tmp_path, absent.name) == []


def test_interruption_and_sidecars_are_cleaned_without_publication(
    tmp_path: Path,
) -> None:
    """Base exceptions and incomplete WAL artifacts leave no owned files behind."""
    target = tmp_path / "interrupted.sqlite"
    with pytest.raises(KeyboardInterrupt):
        with SqliteMigrationArtifact.create(target, _source_metadata()) as artifact:
            artifact.staging_path.write_bytes(b"partial")
            raise KeyboardInterrupt
    assert not target.exists()
    assert _workspace_paths(tmp_path, target.name) == []


async def test_publish_rejects_live_sidecars_and_unlinks_them(tmp_path: Path) -> None:
    """A non-self-contained SQLite candidate is never exposed as final."""
    target = tmp_path / "sidecars.sqlite"

    async def verify(_path: Path, _metadata: SourceManifestMetadata) -> None:
        raise AssertionError("validator must not see a candidate with sidecars")

    with pytest.raises(MigrationArtifactStateError, match="sidecar"):
        with SqliteMigrationArtifact.create(target, _source_metadata()) as artifact:
            _write_valid_database(artifact.staging_path)
            Path(f"{artifact.staging_path}-wal").write_bytes(b"live")
            Path(f"{artifact.staging_path}-shm").write_bytes(b"live")
            await artifact.publish(verify)
    assert not target.exists()
    assert _workspace_paths(tmp_path, target.name) == []


async def test_parent_fsync_failure_removes_just_linked_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed durability barrier does not leave an unconfirmed final artifact."""
    target = tmp_path / "fsync.sqlite"
    real_sync = _sqlite_artifact_files._sync_directory
    calls = 0

    def fail_first_sync(file_descriptor: int) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("fsync failed")
        real_sync(file_descriptor)

    async def verify(_path: Path, _metadata: SourceManifestMetadata) -> None:
        return None

    monkeypatch.setattr(_sqlite_artifact_files, "_sync_directory", fail_first_sync)
    with pytest.raises(OSError, match="fsync failed"):
        with SqliteMigrationArtifact.create(target, _source_metadata()) as artifact:
            _write_valid_database(artifact.staging_path)
            await artifact.publish(verify)
    assert not target.exists()
    assert _workspace_paths(tmp_path, target.name) == []


async def test_replaced_staging_name_is_not_published_or_deleted(tmp_path: Path) -> None:
    """Ownership checks fail closed if the staged name changes inode."""
    target = tmp_path / "replaced.sqlite"

    async def verify(_path: Path, _metadata: SourceManifestMetadata) -> None:
        return None

    artifact = SqliteMigrationArtifact.create(target, _source_metadata())
    workspace = artifact.staging_path.parent
    artifact.staging_path.unlink()
    _write_valid_database(artifact.staging_path)
    with pytest.raises(MigrationArtifactStateError, match="ownership"):
        await artifact.publish(verify)
    assert not target.exists()
    assert artifact.staging_path.exists()
    artifact.staging_path.unlink()
    workspace.rmdir()
    assert os.path.lexists(workspace) is False
