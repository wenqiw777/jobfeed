"""Typed lifecycle for safely publishing imported SQLite artifacts."""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Literal

from jobfeed.adapters.migration._sqlite_artifact_files import (
    MigrationArtifactStateError,
    _ArtifactWorkspace,
)
from jobfeed.adapters.migration._sqlite_artifact_validation import (
    MigrationArtifactValidationError,
    _validate_sqlite_artifact,
)

_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True, slots=True)
class SourceManifestMetadata:
    """Bind publication to the already-validated source manifest identity."""

    manifest_sha256: str
    format_version: int
    source_backend: str
    source_schema_revision: str
    canonical_row_codec_version: str

    def __post_init__(self) -> None:
        if (
            not isinstance(self.manifest_sha256, str)
            or _SHA256_PATTERN.fullmatch(self.manifest_sha256) is None
        ):
            message = "manifest SHA-256 must be 64 lowercase hexadecimal characters"
            raise ValueError(message)
        if (
            isinstance(self.format_version, bool)
            or not isinstance(self.format_version, int)
            or self.format_version < 1
        ):
            message = "source manifest format version must be a positive integer"
            raise ValueError(message)
        for value, label in (
            (self.source_backend, "source backend"),
            (self.source_schema_revision, "source schema revision"),
            (self.canonical_row_codec_version, "canonical row codec version"),
        ):
            if not isinstance(value, str) or not value.strip():
                message = f"{label} must not be empty"
                raise ValueError(message)


@dataclass(frozen=True, slots=True)
class PublishedSqliteArtifact:
    """Identify a durably published SQLite migration artifact."""

    path: Path
    sqlite_sha256: str
    size_bytes: int
    source_manifest: SourceManifestMetadata


ArtifactValidator = Callable[[Path, SourceManifestMetadata], Awaitable[None]]


class SqliteMigrationArtifact:
    """Own one staged SQLite file until exclusive validated publication."""

    def __init__(
        self,
        workspace: _ArtifactWorkspace,
        source_manifest: SourceManifestMetadata,
    ) -> None:
        self._workspace = workspace
        self._source_manifest = source_manifest
        self._state = "staged"

    @classmethod
    def create(
        cls,
        target: Path,
        source_manifest: SourceManifestMetadata,
    ) -> SqliteMigrationArtifact:
        """Create a private same-filesystem stage for an absent final target.

        Args:
            target: Final SQLite path, which must not already exist.
            source_manifest: Identity metadata from the validated source manifest.

        Returns:
            An artifact lifecycle holding the target's exclusive lifecycle lock.

        Raises:
            FileExistsError: If the target or a target sidecar already exists.
            SqliteLifecycleBusyError: If a runtime lifecycle holds the target lock.
        """
        return cls(_ArtifactWorkspace.create(target), source_manifest)

    @property
    def staging_path(self) -> Path:
        """Return the private path into which an importer writes SQLite data.

        Returns:
            Run-owned SQLite staging path with mode 0600.
        """
        return self._workspace.staging_path

    async def publish(
        self,
        validator: ArtifactValidator,
    ) -> PublishedSqliteArtifact:
        """Validate, sync, and publish without replacing any filesystem object.

        Args:
            validator: Import-specific parity callback bound to source metadata.

        Returns:
            Durable artifact identity, digest, size, and source metadata.

        Raises:
            MigrationArtifactValidationError: If SQLite integrity checks fail.
            MigrationArtifactStateError: If staging ownership or sidecars are unsafe.
            FileExistsError: If another publisher creates the target first.
        """
        self._require_staged()
        try:
            self._workspace.assert_owned_stage()
            self._workspace.assert_no_sidecars()
            await _validate_sqlite_artifact(self.staging_path)
            await validator(self.staging_path, self._source_manifest)
            self._workspace.assert_owned_stage()
            self._workspace.assert_no_sidecars()
            await _validate_sqlite_artifact(self.staging_path)
            sqlite_sha256, size_bytes = self._workspace.sync_and_hash()
            path = self._workspace.publish_no_replace()
        except BaseException as error:
            self._state = "failed"
            self._abort_without_masking(error)
            raise
        self._state = "published"
        return PublishedSqliteArtifact(
            path=path,
            sqlite_sha256=sqlite_sha256,
            size_bytes=size_bytes,
            source_manifest=self._source_manifest,
        )

    def abort(self) -> None:
        """Discard this run's unpublished stage and release its lifecycle lock."""
        if self._state != "staged":
            return
        self._state = "aborted"
        self._workspace.abort()

    def __enter__(self) -> SqliteMigrationArtifact:
        """Return this artifact for context-managed importer composition."""
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> Literal[False]:
        """Clean unpublished state without suppressing importer failures."""
        del exception_type, traceback
        if exception is None:
            self.abort()
        else:
            self._abort_without_masking(exception)
        return False

    def _require_staged(self) -> None:
        if self._state != "staged":
            message = f"SQLite migration artifact is already {self._state}"
            raise MigrationArtifactStateError(message)

    def _abort_without_masking(self, original: BaseException) -> None:
        if not self._workspace.closed:
            try:
                self._workspace.abort()
            except OSError as cleanup_error:
                original.add_note(f"artifact cleanup failed: {cleanup_error}")


__all__ = [
    "ArtifactValidator",
    "MigrationArtifactStateError",
    "MigrationArtifactValidationError",
    "PublishedSqliteArtifact",
    "SourceManifestMetadata",
    "SqliteMigrationArtifact",
]
