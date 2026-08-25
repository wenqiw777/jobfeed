"""Fail-closed parity verification for a PostgreSQL manifest and SQLite v1."""

from __future__ import annotations

from dataclasses import dataclass
from typing import NoReturn

import aiosqlite

from jobfeed.adapters.migration._baseline_evidence import _validate_manifest
from jobfeed.adapters.migration._baseline_evidence_shape import mapping
from jobfeed.adapters.migration._baseline_workload import artifact_sha256
from jobfeed.adapters.migration._sqlite_parity_aggregates import (
    capture_sqlite_aggregate_manifest,
)
from jobfeed.adapters.migration._sqlite_parity_reader import (
    SqliteTableMetric,
    read_sqlite_table_metrics,
    sqlite_foreign_key_failures,
    validate_sqlite_integrity,
    validate_sqlite_v1,
)
from jobfeed.adapters.store.sqlite_lifecycle import SqliteLifecycle


@dataclass(frozen=True, kw_only=True)
class ParityMismatch:
    """One typed fail-closed difference between source and target evidence."""

    scope: str
    subject: str
    expected: object
    actual: object


@dataclass(frozen=True, kw_only=True)
class TableParityResult:
    """One target table proven equal to its source manifest metric."""

    table_name: str
    row_count: int
    max_identity: int | None
    canonical_sha256: str


@dataclass(frozen=True, kw_only=True)
class AggregateParityResult:
    """The source-bound counts and hashes reproduced from SQLite."""

    as_of_utc: str
    window_days: int
    pending_stage_a: int
    pending_stage_b: int
    needs_attention_sha256: str
    funnel_sha256: str
    daily_cost_sha256: str
    llm_percentiles_sha256: str


@dataclass(frozen=True, kw_only=True)
class SqliteParityReport:
    """Typed success or failure report for one consistent target snapshot."""

    report_version: int
    is_match: bool
    manifest_sha256: str | None
    sqlite_schema_version: int | None
    tables: tuple[TableParityResult, ...]
    aggregates: AggregateParityResult | None
    mismatches: tuple[ParityMismatch, ...]


class SqliteParityVerificationError(ValueError):
    """Fail-closed parity error carrying the complete typed report."""

    def __init__(self, report: SqliteParityReport) -> None:
        """Create an error for a report containing at least one mismatch.

        Args:
            report: Typed failed parity report.
        """
        self.report = report
        first = report.mismatches[0]
        super().__init__(f"SQLite parity mismatch at {first.scope}.{first.subject}")


async def verify_sqlite_parity(
    lifecycle: SqliteLifecycle,
    manifest: object,
    *,
    chunk_size: int = 1000,
) -> SqliteParityReport:
    """Verify exact 14-table and business-aggregate parity.

    The target is read in one SQLite transaction. This function never imports,
    repairs, or replaces data and returns only when every comparison succeeds.

    Args:
        lifecycle: Open lifecycle for the SQLite-v1 migration target.
        manifest: Parsed, exact PostgreSQL snapshot manifest.
        chunk_size: Positive number of ordered rows fetched per hash chunk.

    Returns:
        Typed report proving exact schema, rows, hashes, and aggregates.

    Raises:
        ValueError: If chunk size is invalid.
        SqliteParityVerificationError: If any manifest or target check differs.
        Exception: Propagates lifecycle and database access failures.

    Complexity:
        O(R) time and O(chunk_size) row memory for R migrated rows.
    """
    if type(chunk_size) is not int or chunk_size <= 0:
        raise ValueError("SQLite parity chunk_size must be a positive integer")
    manifest_document, manifest_sha = _validated_manifest(manifest)
    async with lifecycle.connection() as connection:
        connection.row_factory = aiosqlite.Row
        await connection.execute("BEGIN")
        try:
            return await _verify_snapshot(
                connection,
                manifest_document,
                manifest_sha=manifest_sha,
                chunk_size=chunk_size,
            )
        finally:
            await connection.rollback()


def _validated_manifest(manifest: object) -> tuple[dict[str, object], str]:
    try:
        document = mapping(manifest, "manifest")
        _validate_manifest(document)
        return document, artifact_sha256(document)
    except (KeyError, TypeError, ValueError) as exc:
        _raise_failure("manifest", "document", "exact snapshot manifest", str(exc))


async def _verify_snapshot(
    connection: aiosqlite.Connection,
    manifest: dict[str, object],
    *,
    manifest_sha: str,
    chunk_size: int,
) -> SqliteParityReport:
    try:
        version = await validate_sqlite_v1(connection)
        await validate_sqlite_integrity(connection)
    except (aiosqlite.Error, TypeError, ValueError) as exc:
        _raise_failure(
            "sqlite_schema", "current", "exact current SQLite schema", str(exc)
        )
    foreign_keys = await sqlite_foreign_key_failures(connection)
    if foreign_keys:
        _raise_failure("foreign_key", "all", (), foreign_keys)
    try:
        actual_tables = await read_sqlite_table_metrics(
            connection, chunk_size=chunk_size
        )
    except (aiosqlite.Error, TypeError, ValueError) as exc:
        _raise_failure("table", "canonical_rows", "codec-v1 rows", str(exc))
    expected_tables = mapping(manifest["tables"], "manifest.tables")
    mismatches = _table_mismatches(actual_tables, expected_tables)
    aggregate_document = mapping(manifest["aggregates"], "manifest.aggregates")
    try:
        actual_aggregates = await capture_sqlite_aggregate_manifest(
            connection, as_of_utc=str(aggregate_document["as_of_utc"])
        )
    except (aiosqlite.Error, TypeError, ValueError) as exc:
        _raise_failure("aggregate", "capture", "canonical aggregates", str(exc))
    mismatches.extend(_aggregate_mismatches(actual_aggregates, aggregate_document))
    report = SqliteParityReport(
        report_version=1,
        is_match=not mismatches,
        manifest_sha256=manifest_sha,
        sqlite_schema_version=version,
        tables=tuple(TableParityResult(**vars(item)) for item in actual_tables),
        aggregates=_aggregate_result(actual_aggregates),
        mismatches=tuple(mismatches),
    )
    if mismatches:
        raise SqliteParityVerificationError(report)
    return report


def _table_mismatches(
    actual: tuple[SqliteTableMetric, ...], expected: dict[str, object]
) -> list[ParityMismatch]:
    """Compare fields; time complexity is O(table count * metric count)."""
    differences = []
    for metric in actual:
        source = mapping(expected[metric.table_name], metric.table_name)
        for field, value in (
            ("row_count", metric.row_count),
            ("max_identity", metric.max_identity),
            ("canonical_sha256", metric.canonical_sha256),
        ):
            if source[field] != value:
                differences.append(
                    ParityMismatch(
                        scope="table",
                        subject=f"{metric.table_name}.{field}",
                        expected=source[field],
                        actual=value,
                    )
                )
    return differences


def _aggregate_mismatches(
    actual: dict[str, object], expected: dict[str, object]
) -> list[ParityMismatch]:
    return [
        ParityMismatch(
            scope="aggregate",
            subject=field,
            expected=expected[field],
            actual=value,
        )
        for field, value in actual.items()
        if expected[field] != value
    ]


def _aggregate_result(document: dict[str, object]) -> AggregateParityResult:
    return AggregateParityResult(
        as_of_utc=_text(document["as_of_utc"]),
        window_days=_int(document["window_days"]),
        pending_stage_a=_int(document["pending_stage_a"]),
        pending_stage_b=_int(document["pending_stage_b"]),
        needs_attention_sha256=_text(document["needs_attention_sha256"]),
        funnel_sha256=_text(document["funnel_sha256"]),
        daily_cost_sha256=_text(document["daily_cost_sha256"]),
        llm_percentiles_sha256=_text(document["llm_percentiles_sha256"]),
    )


def _raise_failure(
    scope: str, subject: str, expected: object, actual: object
) -> NoReturn:
    mismatch = ParityMismatch(
        scope=scope,
        subject=subject,
        expected=expected,
        actual=actual,
    )
    raise SqliteParityVerificationError(
        SqliteParityReport(
            report_version=1,
            is_match=False,
            manifest_sha256=None,
            sqlite_schema_version=None,
            tables=(),
            aggregates=None,
            mismatches=(mismatch,),
        )
    )


def _int(value: object) -> int:
    if type(value) is not int:
        raise ValueError(f"parity report value is not an integer: {value!r}")
    return value


def _text(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError(f"parity report value is not text: {value!r}")
    return value


__all__ = [
    "AggregateParityResult",
    "ParityMismatch",
    "SqliteParityReport",
    "SqliteParityVerificationError",
    "TableParityResult",
    "verify_sqlite_parity",
]
