"""Import one isolated PostgreSQL restore and prove SQLite parity."""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from jobfeed.adapters.migration._baseline_workload import artifact_sha256
from jobfeed.adapters.migration._pg_baseline_manifest import (
    SnapshotManifestContext,
    build_snapshot_manifest,
)
from jobfeed.adapters.migration._pg_baseline_reader import PostgresBaselineReader
from jobfeed.adapters.migration.pg_baseline import (
    _gate_state,
    validate_live_schema,
    validate_public_tables,
)
from jobfeed.adapters.migration.sqlite_forward_import import (
    import_postgres_snapshot_to_sqlite,
)
from jobfeed.adapters.migration.sqlite_parity import (
    SqliteParityReport,
    verify_sqlite_parity,
)
from jobfeed.adapters.store.sqlite_lifecycle import SqliteLifecycle
from jobfeed.adapters.store.sqlite_schema import ensure_sqlite_schema


@dataclass(frozen=True, kw_only=True)
class CutoverSourceEvidence:
    """Immutable dump and restore evidence for one isolated source."""

    git_commit: str
    dump_sha256: str
    dump_size_bytes: int
    restore_attestations: dict[str, object]


@dataclass(frozen=True, kw_only=True)
class CutoverRehearsalResult:
    """Exact JSON documents emitted beside the proven SQLite file."""

    manifest: dict[str, object]
    import_result: dict[str, object]
    parity_result: dict[str, object]
    index: dict[str, object]


def run_cutover_rehearsal(
    dsn: str,
    *,
    destination: Path,
    source: CutoverSourceEvidence,
    chunk_size: int = 1_000,
) -> CutoverRehearsalResult:
    """Import one quiescent restored snapshot and verify exact SQLite parity.

    Args:
        dsn: Internal Compose DSN for the isolated PostgreSQL source.
        destination: New SQLite file inside a private artifact staging directory.
        source: Dump identity, code revision, and restore attestations.
        chunk_size: Bounded PostgreSQL and SQLite row fetch size.

    Returns:
        Manifest, physical import, logical parity, and one-way index documents.

    Raises:
        ValueError: Source identity, schema, quiescence, or parity differs.
        FileExistsError: The destination already exists.
        Exception: PostgreSQL or SQLite failures propagate without final publish.

    Complexity:
        O(rows * columns) time and O(chunk_size) row memory.
    """
    if type(chunk_size) is not int or chunk_size <= 0:
        raise ValueError("cutover rehearsal chunk_size must be a positive integer")
    with PostgresBaselineReader(dsn) as reader:
        revision, active_writers, running_runs = _gate_state(reader)
        validate_public_tables(reader.public_base_tables())
        validate_live_schema(reader.live_schema_document())
        _require_source_identity(reader, source.restore_attestations)
        manifest = build_snapshot_manifest(
            reader,
            context=SnapshotManifestContext(
                dsn=dsn,
                captured_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
                git_commit=source.git_commit,
                dump_sha256=source.dump_sha256,
                dump_size_bytes=source.dump_size_bytes,
                revision=revision,
                active_writers=active_writers,
                running_runs=running_runs,
                restore_attestations=source.restore_attestations,
            ),
            chunk_size=chunk_size,
        )
        imported = import_postgres_snapshot_to_sqlite(
            reader, manifest, destination, chunk_size=chunk_size
        )
    parity = asyncio.run(_verify_published_sqlite(destination, manifest, chunk_size))
    sqlite_sha256 = _closed_file_sha256(destination)
    if Path(f"{destination}-wal").exists() or Path(f"{destination}-shm").exists():
        raise ValueError("cutover SQLite target retained live WAL sidecars")
    import_document = {
        "result_version": 1,
        "sqlite_filename": destination.name,
        "sqlite_file_sha256": sqlite_sha256,
        "row_counts": imported.row_counts,
        "table_sha256": imported.table_sha256,
    }
    parity_document = asdict(parity)
    index = _evidence_index(
        manifest,
        import_document,
        parity_document,
        source=source,
        sqlite_sha256=sqlite_sha256,
    )
    validate_cutover_evidence(manifest, import_document, parity_document, index)
    return CutoverRehearsalResult(
        manifest=manifest,
        import_result=import_document,
        parity_result=parity_document,
        index=index,
    )


async def _verify_published_sqlite(
    path: Path, manifest: object, chunk_size: int
) -> SqliteParityReport:
    lifecycle = SqliteLifecycle(path, ensure_sqlite_schema)
    await lifecycle.open()
    try:
        return await verify_sqlite_parity(lifecycle, manifest, chunk_size=chunk_size)
    finally:
        await lifecycle.close()


def _closed_file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _require_source_identity(
    reader: PostgresBaselineReader, attestations: dict[str, object]
) -> None:
    source = attestations.get("source")
    if not isinstance(source, dict):
        raise ValueError("cutover source restore attestation is required")
    if reader.database_identity() != source.get("database_identity"):
        raise ValueError("cutover source database identity differs from attestation")


def _evidence_index(
    manifest: object,
    import_result: object,
    parity_result: object,
    *,
    source: CutoverSourceEvidence,
    sqlite_sha256: str,
) -> dict[str, object]:
    return {
        "cutover_evidence_version": 1,
        "source_dump_sha256": source.dump_sha256,
        "git_commit": source.git_commit,
        "manifest_sha256": artifact_sha256(manifest),
        "import_result_sha256": artifact_sha256(import_result),
        "parity_result_sha256": artifact_sha256(parity_result),
        "sqlite_file_sha256": sqlite_sha256,
    }


def validate_cutover_evidence(
    manifest: object,
    import_result: object,
    parity_result: object,
    index: object,
) -> None:
    """Validate exact cross-links among cutover evidence documents.

    Args:
        manifest: PostgreSQL source snapshot manifest.
        import_result: Physical SQLite import result.
        parity_result: Logical SQLite parity result.
        index: Candidate one-way cutover evidence index.

    Raises:
        ValueError: The index schema or any evidence hash differs.
    """
    if not isinstance(index, dict) or set(index) != {
        "cutover_evidence_version",
        "source_dump_sha256",
        "git_commit",
        "manifest_sha256",
        "import_result_sha256",
        "parity_result_sha256",
        "sqlite_file_sha256",
    }:
        raise ValueError("cutover evidence index exact schema mismatch")
    if index["cutover_evidence_version"] != 1:
        raise ValueError("unknown cutover evidence version")
    expected = {
        "manifest_sha256": artifact_sha256(manifest),
        "import_result_sha256": artifact_sha256(import_result),
        "parity_result_sha256": artifact_sha256(parity_result),
    }
    if any(index[key] != value for key, value in expected.items()):
        raise ValueError("cutover evidence index hash mismatch")
    if not isinstance(manifest, dict):
        raise ValueError("cutover manifest must be an object")
    manifest_source = manifest.get("source")
    if not isinstance(manifest_source, dict) or (
        manifest_source.get("source_dump_sha256") != index["source_dump_sha256"]
        or manifest.get("git_commit") != index["git_commit"]
    ):
        raise ValueError("cutover manifest provenance cross-link mismatch")
    if not isinstance(import_result, dict) or (
        import_result.get("sqlite_file_sha256") != index["sqlite_file_sha256"]
    ):
        raise ValueError("cutover SQLite file hash cross-link mismatch")
    if (
        not isinstance(parity_result, dict)
        or parity_result.get("is_match") is not True
        or parity_result.get("manifest_sha256") != index["manifest_sha256"]
    ):
        raise ValueError("cutover parity result is not a match")


__all__ = [
    "CutoverRehearsalResult",
    "CutoverSourceEvidence",
    "run_cutover_rehearsal",
    "validate_cutover_evidence",
]
