"""Fail-closed read-only verification of a completed PostgreSQL rollback."""

from __future__ import annotations

from jobfeed.adapters.migration import _pg_rollback_capture as capture
from jobfeed.adapters.migration._pg_rollback_capture import (
    PostgresRollbackReader,
    _capture_sequence_results,
    _capture_table_results,
    _read_trigger_code,
)
from jobfeed.adapters.migration._pg_rollback_compare import (
    _aggregate_mismatches,
    _aggregate_result,
    _raise_failure,
    _ReportContext,
    _sequence_mismatches,
    _table_mismatches,
)
from jobfeed.adapters.migration._pg_rollback_evidence import (
    ValidatedRollbackSource,
    validate_cutover_provenance,
    validate_rollback_source,
)
from jobfeed.adapters.migration._pg_rollback_verifier_types import (
    AggregateVerificationResult,
    ExpectedCutoverProvenance,
    PostgresRollbackVerificationError,
    PostgresRollbackVerificationReport,
    RollbackSourceManifest,
    RollbackVerificationMismatch,
    SequenceVerificationResult,
    TableVerificationResult,
)
from jobfeed.adapters.migration.canonical_schema_manifest import (
    MIGRATED_TABLE_ORDER_V1,
    validate_schema_manifest,
)

_EXPECTED_PUBLIC_TABLES = tuple(sorted((*MIGRATED_TABLE_ORDER_V1, "alembic_version")))


def verify_postgres_rollback(
    reader: PostgresRollbackReader,
    source_manifest: RollbackSourceManifest | dict[str, object],
    expected_cutover: ExpectedCutoverProvenance,
    *,
    chunk_size: int = 1000,
) -> PostgresRollbackVerificationReport:
    """Verify post-rollback PostgreSQL state against immutable evidence.

    The caller supplies an active repeatable-read, read-only PostgreSQL reader.
    This operation does not repair, import, reset, or otherwise mutate the target.

    Args:
        reader: Active read-only PostgreSQL snapshot reader.
        source_manifest: Exact closed SQLite source manifest from Task 5A.
        expected_cutover: Writer preflight proof bound to the cutover manifest.
        chunk_size: Positive canonical row streaming chunk size.

    Returns:
        Typed proof of schema, conflict, table, sequence, and aggregate parity.

    Raises:
        ValueError: If chunk size is invalid.
        PostgresRollbackVerificationError: If any evidence or target check fails.

    Complexity:
        O(R) time and O(chunk_size) memory for R rows across 14 tables.
    """
    if type(chunk_size) is not int or chunk_size <= 0:
        raise ValueError("PostgreSQL rollback chunk_size must be a positive integer")
    source = _validated_source(source_manifest)
    cutover_sha = _validated_cutover(expected_cutover)
    try:
        identity = reader.database_identity()
    except Exception as error:
        _raise_failure(
            "cutover_provenance",
            "database_identity",
            expected_cutover.target_database_identity,
            str(error),
            context=_ReportContext(
                source_sha=source.manifest_sha256,
                cutover_sha=cutover_sha,
            ),
        )
    if identity != expected_cutover.target_database_identity:
        _raise_failure(
            "cutover_provenance",
            "database_identity",
            expected_cutover.target_database_identity,
            identity,
            context=_ReportContext(
                source_sha=source.manifest_sha256,
                cutover_sha=cutover_sha,
            ),
        )
    revision = _verify_live_schema(
        reader,
        source=source,
        cutover_sha=cutover_sha,
        identity=identity,
    )
    trigger_enabled = _verify_trigger(
        reader,
        source=source,
        cutover_sha=cutover_sha,
        identity=identity,
        revision=revision,
    )
    context = _ReportContext(
        source_sha=source.manifest_sha256,
        cutover_sha=cutover_sha,
        identity=identity,
        revision=revision,
        trigger_enabled=trigger_enabled,
    )
    tables = _capture_tables(reader, context, chunk_size)
    mismatches = _table_mismatches(tables, source.tables)
    sequences = _capture_sequences(reader, tables, source, cutover_sha, identity)
    mismatches.extend(_sequence_mismatches(sequences))
    aggregates = _capture_aggregate_result(reader, source, cutover_sha, identity)
    mismatches.extend(_aggregate_mismatches(aggregates, source.aggregates))
    report = PostgresRollbackVerificationReport(
        report_version=1,
        is_match=not mismatches,
        source_manifest_sha256=source.manifest_sha256,
        cutover_manifest_sha256=cutover_sha,
        database_identity=identity,
        alembic_revision=revision,
        trigger_enabled=trigger_enabled,
        tables=tables,
        sequences=sequences,
        aggregates=aggregates,
        mismatches=tuple(mismatches),
    )
    if mismatches:
        raise PostgresRollbackVerificationError(report)
    return report


def _validated_source(value: object) -> ValidatedRollbackSource:
    try:
        return validate_rollback_source(value)
    except Exception as error:
        _raise_failure(
            "source_manifest", "document", "exact Task 5A manifest", str(error)
        )


def _validated_cutover(proof: ExpectedCutoverProvenance) -> str:
    try:
        return validate_cutover_provenance(proof)
    except (KeyError, TypeError, ValueError) as error:
        _raise_failure(
            "cutover_provenance", "proof", "exact cutover conflict proof", str(error)
        )


def _verify_live_schema(
    reader: PostgresRollbackReader,
    *,
    source: ValidatedRollbackSource,
    cutover_sha: str,
    identity: str,
) -> str:
    try:
        revision = reader.scalar("SELECT version_num FROM alembic_version")
        if revision != "0008":
            raise ValueError(f"expected revision 0008, got {revision!r}")
        tables = tuple(reader.public_base_tables())
        if tables != _EXPECTED_PUBLIC_TABLES:
            raise ValueError(
                f"public table coverage differs: expected={_EXPECTED_PUBLIC_TABLES}, "
                f"actual={tables}"
            )
        validate_schema_manifest(reader.live_schema_document())
    except Exception as error:
        _raise_failure(
            "postgres_schema",
            "0008",
            "exact 14 tables plus alembic_version",
            str(error),
            context=_ReportContext(
                source_sha=source.manifest_sha256,
                cutover_sha=cutover_sha,
                identity=identity,
            ),
        )
    return "0008"


def _verify_trigger(
    reader: PostgresRollbackReader,
    *,
    source: ValidatedRollbackSource,
    cutover_sha: str,
    identity: str,
    revision: str,
) -> bool:
    try:
        code = _read_trigger_code(reader)
    except Exception as error:
        code = str(error)
    if code != "O":
        _raise_failure(
            "trigger",
            "trg_jobs_seed_status",
            "O",
            code,
            context=_ReportContext(
                source_sha=source.manifest_sha256,
                cutover_sha=cutover_sha,
                identity=identity,
                revision=revision,
            ),
        )
    return True


def _capture_tables(
    reader: PostgresRollbackReader,
    context: _ReportContext,
    chunk_size: int,
) -> tuple[TableVerificationResult, ...]:
    try:
        return _capture_table_results(reader, chunk_size)
    except Exception as error:
        _raise_failure(
            "table",
            "canonical_capture",
            "14 ordered canonical table metrics",
            str(error),
            context=context,
        )


def _capture_sequences(
    reader: PostgresRollbackReader,
    tables: tuple[TableVerificationResult, ...],
    source: ValidatedRollbackSource,
    cutover_sha: str,
    identity: str,
) -> tuple[SequenceVerificationResult, ...]:
    try:
        return _capture_sequence_results(reader, tables)
    except Exception as error:
        _raise_failure(
            "sequence",
            "capture",
            "all generated identity sequences",
            str(error),
            context=_ReportContext(
                source_sha=source.manifest_sha256,
                cutover_sha=cutover_sha,
                identity=identity,
                tables=tables,
            ),
        )


def _capture_aggregate_result(
    reader: PostgresRollbackReader,
    source: ValidatedRollbackSource,
    cutover_sha: str,
    identity: str,
) -> AggregateVerificationResult:
    try:
        document = capture._capture_aggregate_manifest(reader, source.aggregate_as_of)
        return _aggregate_result(document)
    except Exception as error:
        _raise_failure(
            "aggregate",
            "capture",
            "source-bound aggregate hashes",
            str(error),
            context=_ReportContext(
                source_sha=source.manifest_sha256,
                cutover_sha=cutover_sha,
                identity=identity,
            ),
        )


__all__ = [
    "AggregateVerificationResult",
    "ExpectedCutoverProvenance",
    "PostgresRollbackReader",
    "PostgresRollbackVerificationError",
    "PostgresRollbackVerificationReport",
    "RollbackSourceManifest",
    "RollbackVerificationMismatch",
    "SequenceVerificationResult",
    "TableVerificationResult",
    "verify_postgres_rollback",
]
