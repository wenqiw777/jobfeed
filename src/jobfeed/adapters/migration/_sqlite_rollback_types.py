"""Typed evidence emitted by a SQLite rollback source snapshot."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from jobfeed.adapters.migration._sqlite_parity_reader import SqliteTableMetric

ROLLBACK_MANIFEST_VERSION: Final = 1


@dataclass(frozen=True, kw_only=True)
class SqliteRollbackSourceIdentity:
    """Path-free physical identity of one closed SQLite source file."""

    file_size_bytes: int
    file_sha256: str
    device: int
    inode: int
    journal_mode: str
    has_wal: bool


@dataclass(frozen=True, kw_only=True)
class SqliteRollbackTableMetric:
    """One canonical source table's primary key, count, maximum, and hash."""

    table_name: str
    primary_key: tuple[str, ...]
    row_count: int
    max_identity: int | None
    canonical_sha256: str


@dataclass(frozen=True, kw_only=True)
class SqliteRollbackAggregates:
    """Source-bound business counts and aggregate hashes."""

    as_of_utc: str
    window_days: int
    pending_stage_a: int
    pending_stage_b: int
    needs_attention_sha256: str
    funnel_sha256: str
    daily_cost_sha256: str
    llm_percentiles_sha256: str


@dataclass(frozen=True, kw_only=True)
class SqliteRollbackManifest:
    """Exact logical and physical identity of one SQLite rollback source."""

    manifest_version: int
    created_at_utc: str
    sqlite_schema_version: int
    schema_registry: dict[str, object]
    source: SqliteRollbackSourceIdentity
    tables: tuple[SqliteRollbackTableMetric, ...]
    aggregates: SqliteRollbackAggregates


def rollback_table_metrics(
    metrics: tuple[SqliteTableMetric, ...],
    primary_keys: dict[str, tuple[str, ...]],
) -> tuple[SqliteRollbackTableMetric, ...]:
    """Add canonical primary-key declarations to captured table metrics.

    Args:
        metrics: Canonical count/hash metrics from the SQLite reader.
        primary_keys: Exact registry primary keys by table name.

    Returns:
        Registry-ordered immutable rollback table metrics.
    """
    return tuple(
        SqliteRollbackTableMetric(
            table_name=metric.table_name,
            primary_key=primary_keys[metric.table_name],
            row_count=metric.row_count,
            max_identity=metric.max_identity,
            canonical_sha256=metric.canonical_sha256,
        )
        for metric in metrics
    )


def rollback_aggregates(document: dict[str, object]) -> SqliteRollbackAggregates:
    """Convert an internally validated aggregate document to a typed value.

    Args:
        document: Exact aggregate manifest emitted by the shared implementation.

    Returns:
        Immutable typed aggregate evidence.

    Raises:
        ValueError: If a field is not the already validated scalar type.
    """
    return SqliteRollbackAggregates(
        as_of_utc=_text(document["as_of_utc"]),
        window_days=_integer(document["window_days"]),
        pending_stage_a=_integer(document["pending_stage_a"]),
        pending_stage_b=_integer(document["pending_stage_b"]),
        needs_attention_sha256=_text(document["needs_attention_sha256"]),
        funnel_sha256=_text(document["funnel_sha256"]),
        daily_cost_sha256=_text(document["daily_cost_sha256"]),
        llm_percentiles_sha256=_text(document["llm_percentiles_sha256"]),
    )


def _integer(value: object) -> int:
    if type(value) is not int:
        raise ValueError(f"rollback aggregate is not an integer: {value!r}")
    return value


def _text(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError(f"rollback aggregate is not text: {value!r}")
    return value
