"""Owned filesystem state for exclusive SQLite migration publication."""

from __future__ import annotations

import contextlib
import hashlib
import os
import stat
from dataclasses import dataclass
from pathlib import Path

from jobfeed.adapters.migration._sqlite_artifact_ownership import (
    DIRECTORY_FLAGS,
    NOFOLLOW,
    SIDECAR_SUFFIXES,
    STAGE_NAME,
    _assert_target_absent,
    _canonical_target,
    _clean_failed_creation,
    _create_workspace,
    _entry_exists,
    _OwnedIdentity,
)
from jobfeed.adapters.store._sqlite_lock import DatabaseFileLock


class MigrationArtifactStateError(RuntimeError):
    """Raised when artifact ownership or publication state is unsafe."""


@dataclass
class _ArtifactWorkspace:
    """Own a private same-filesystem stage and the target lifecycle lock."""

    target: Path
    workspace_name: str
    parent_fd: int
    workspace_fd: int
    stage_fd: int
    workspace_identity: _OwnedIdentity
    stage_identity: _OwnedIdentity
    database_lock: DatabaseFileLock
    closed: bool = False

    @property
    def staging_path(self) -> Path:
        """Return the importer-owned candidate path."""
        return self.target.parent / self.workspace_name / STAGE_NAME

    @classmethod
    def create(cls, target: Path) -> _ArtifactWorkspace:
        canonical = _canonical_target(target)
        parent_fd = os.open(canonical.parent, DIRECTORY_FLAGS | NOFOLLOW)
        database_lock = DatabaseFileLock(canonical)
        workspace_name: str | None = None
        workspace_fd: int | None = None
        stage_fd: int | None = None
        try:
            database_lock.acquire_exclusive()
            _assert_target_absent(parent_fd, canonical.name)
            workspace_name = _create_workspace(parent_fd, canonical.name)
            workspace_fd = os.open(
                workspace_name,
                DIRECTORY_FLAGS | NOFOLLOW,
                dir_fd=parent_fd,
            )
            os.fchmod(workspace_fd, 0o700)
            workspace_stat = os.fstat(workspace_fd)
            stage_fd = os.open(
                STAGE_NAME,
                os.O_RDWR
                | os.O_CREAT
                | os.O_EXCL
                | NOFOLLOW
                | getattr(os, "O_CLOEXEC", 0),
                0o600,
                dir_fd=workspace_fd,
            )
            os.fchmod(stage_fd, 0o600)
            return cls(
                target=canonical,
                workspace_name=workspace_name,
                parent_fd=parent_fd,
                workspace_fd=workspace_fd,
                stage_fd=stage_fd,
                workspace_identity=_OwnedIdentity._from_stat(workspace_stat),
                stage_identity=_OwnedIdentity._from_stat(os.fstat(stage_fd)),
                database_lock=database_lock,
            )
        except BaseException:
            _clean_failed_creation(
                parent_fd=parent_fd,
                workspace_name=workspace_name,
                workspace_fd=workspace_fd,
                stage_fd=stage_fd,
                database_lock=database_lock,
            )
            raise

    def assert_owned_stage(self) -> None:
        """Fail closed if either run-owned filesystem identity changed."""
        if not self._workspace_is_owned() or not self._stage_is_owned():
            message = "SQLite migration staging ownership changed"
            raise MigrationArtifactStateError(message)

    def assert_no_sidecars(self) -> None:
        """Reject a candidate that is not a self-contained SQLite file."""
        for suffix in SIDECAR_SUFFIXES:
            if _entry_exists(self.workspace_fd, f"{STAGE_NAME}{suffix}"):
                message = "SQLite migration artifact has a live sidecar"
                raise MigrationArtifactStateError(message)

    def sync_and_hash(self) -> tuple[str, int]:
        """Durably flush and hash the held staged file descriptor."""
        self.assert_owned_stage()
        os.fsync(self.stage_fd)
        file_stat = os.fstat(self.stage_fd)
        digest = hashlib.sha256()
        offset = 0
        while offset < file_stat.st_size:
            chunk = os.pread(self.stage_fd, 1024 * 1024, offset)
            if not chunk:
                break
            digest.update(chunk)
            offset += len(chunk)
        if offset != file_stat.st_size:
            message = "SQLite migration staging file changed while hashing"
            raise MigrationArtifactStateError(message)
        return digest.hexdigest(), file_stat.st_size

    def publish_no_replace(self) -> Path:
        """Link the owned inode to an absent target and cross a durability barrier."""
        self.assert_owned_stage()
        self.assert_no_sidecars()
        linked = False
        try:
            os.link(
                STAGE_NAME,
                self.target.name,
                src_dir_fd=self.workspace_fd,
                dst_dir_fd=self.parent_fd,
                follow_symlinks=False,
            )
            linked = True
            if not self._target_is_owned():
                message = "published SQLite artifact does not retain staging ownership"
                raise MigrationArtifactStateError(message)
            _sync_directory(self.parent_fd)
            self._remove_owned_stage()
            _sync_directory(self.workspace_fd)
            self._remove_owned_workspace()
            _sync_directory(self.parent_fd)
        except BaseException as error:
            if linked:
                try:
                    self._remove_owned_target()
                    _sync_directory(self.parent_fd)
                except OSError as cleanup_error:
                    error.add_note(
                        f"published artifact rollback failed: {cleanup_error}"
                    )
            raise
        self._close_and_unlock()
        return self.target

    def abort(self) -> None:
        """Remove only run-owned staging objects and release the target lock."""
        if self.closed:
            return
        cleanup_error: OSError | None = None
        try:
            self._remove_owned_sidecars()
            self._remove_owned_stage()
            self._remove_owned_workspace()
            _sync_directory(self.parent_fd)
        except OSError as error:
            cleanup_error = error
        finally:
            self._close_and_unlock()
        if cleanup_error is not None:
            raise cleanup_error

    def _workspace_is_owned(self) -> bool:
        try:
            value = os.stat(
                self.workspace_name,
                dir_fd=self.parent_fd,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            return False
        return stat.S_ISDIR(value.st_mode) and self.workspace_identity._matches(value)

    def _stage_is_owned(self) -> bool:
        try:
            value = os.stat(STAGE_NAME, dir_fd=self.workspace_fd, follow_symlinks=False)
        except FileNotFoundError:
            return False
        return stat.S_ISREG(value.st_mode) and self.stage_identity._matches(value)

    def _target_is_owned(self) -> bool:
        try:
            value = os.stat(
                self.target.name,
                dir_fd=self.parent_fd,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            return False
        return stat.S_ISREG(value.st_mode) and self.stage_identity._matches(value)

    def _remove_owned_target(self) -> None:
        if self._target_is_owned():
            os.unlink(self.target.name, dir_fd=self.parent_fd)

    def _remove_owned_sidecars(self) -> None:
        if not self._workspace_is_owned():
            return
        for suffix in SIDECAR_SUFFIXES:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(f"{STAGE_NAME}{suffix}", dir_fd=self.workspace_fd)

    def _remove_owned_stage(self) -> None:
        if self._workspace_is_owned() and self._stage_is_owned():
            os.unlink(STAGE_NAME, dir_fd=self.workspace_fd)

    def _remove_owned_workspace(self) -> None:
        if self._workspace_is_owned():
            os.rmdir(self.workspace_name, dir_fd=self.parent_fd)

    def _close_and_unlock(self) -> None:
        if self.closed:
            return
        self.closed = True
        try:
            os.close(self.stage_fd)
        finally:
            try:
                os.close(self.workspace_fd)
            finally:
                try:
                    self.database_lock.release()
                finally:
                    os.close(self.parent_fd)


def _sync_directory(file_descriptor: int) -> None:
    os.fsync(file_descriptor)
