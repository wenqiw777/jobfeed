"""Typed configuration, source, and report contracts for PostgreSQL rollback."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Protocol


class RollbackFaultPoint(Enum):
    """Deterministic transaction boundaries used by rollback injection tests."""

    PREFLIGHT = "preflight"
    AFTER_TRIGGER_DISABLE = "after_trigger_disable"
    AFTER_JOBS = "after_jobs"
    MID_REPLAY = "mid_replay"
    AFTER_SEQUENCE_RESET = "after_sequence_reset"
    BEFORE_TRIGGER_ENABLE = "before_trigger_enable"
    TRIGGER_ENABLE = "trigger_enable"


class CanonicalRollbackSource(Protocol):
    """Open consistent source snapshot that yields all final canonical rows."""

    @property
    def table_metrics(self) -> tuple[CanonicalRollbackTableMetric, ...]:
        """Return metrics captured from this same consistent snapshot.

        Returns:
            Registry-ordered metrics for all fourteen migrated tables.
        """

    def stream_table(
        self, table_name: str, *, chunk_size: int | None = None
    ) -> AsyncIterator[dict[str, object]]:
        """Stream one allowlisted table in canonical PK order.

        Args:
            table_name: Exact migrated table name.
            chunk_size: Positive bounded source fetch size.

        Returns:
            Asynchronous ordered row iterator.
        """


class CanonicalRollbackTableMetric(Protocol):
    """Backend-neutral source metric consumed by the rollback writer."""

    table_name: str
    primary_key: tuple[str, ...]
    row_count: int
    max_identity: int | None
    canonical_sha256: str


@dataclass(frozen=True, kw_only=True)
class RollbackWriterConfig:
    """Connection and bounded-buffer settings for one rollback transaction."""

    dsn: str
    chunk_size: int = 1_000
    fault_point: RollbackFaultPoint | None = None


@dataclass(frozen=True, kw_only=True)
class PostgresRollbackReport:
    """Committed transaction proof returned by the rollback writer."""

    revision: str
    target_was_empty: bool
    trigger_name: str
    trigger_was_enabled: bool
    trigger_is_enabled: bool
    pre_import_table_metrics: dict[str, Mapping[str, object]]
    replayed_rows: dict[str, int]
    deleted_rows: dict[str, int]
    final_table_metrics: dict[str, Mapping[str, object]]


class PostgresRollbackError(ValueError):
    """Fail-closed target or source mismatch detected before commit."""
