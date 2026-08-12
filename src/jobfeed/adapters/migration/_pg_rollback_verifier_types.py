"""Typed evidence and reports for PostgreSQL rollback verification."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class RollbackSourceManifest(Protocol):
    """Structural Task-5A SQLite source manifest accepted by the verifier."""

    manifest_version: int
    created_at_utc: str
    sqlite_schema_version: int
    schema_registry: dict[str, object]
    source: object
    tables: tuple[object, ...]
    aggregates: object


@dataclass(frozen=True, kw_only=True)
class RollbackVerificationMismatch:
    """One fail-closed difference in rollback evidence or target state."""

    scope: str
    subject: str
    expected: object
    actual: object


@dataclass(frozen=True, kw_only=True)
class TableVerificationResult:
    """Canonical count, identity maximum, and hash for one migrated table."""

    table_name: str
    row_count: int
    max_identity: int | None
    canonical_sha256: str


@dataclass(frozen=True, kw_only=True)
class SequenceVerificationResult:
    """One generated identity sequence proven at or beyond persisted IDs."""

    table_name: str
    sequence_name: str
    last_value: int
    max_identity: int | None


@dataclass(frozen=True, kw_only=True)
class AggregateVerificationResult:
    """Source-bound business aggregate hashes reproduced after rollback."""

    as_of_utc: str
    window_days: int
    pending_stage_a: int
    pending_stage_b: int
    needs_attention_sha256: str
    funnel_sha256: str
    daily_cost_sha256: str
    llm_percentiles_sha256: str


@dataclass(frozen=True, kw_only=True)
class ExpectedCutoverProvenance:
    """Writer-produced proof that the target matched the cutover snapshot.

    The proof binds the exact pre-import table metrics to a validated cutover
    manifest and to the same PostgreSQL database identity verified afterward.
    """

    proof_version: int
    cutover_manifest: object
    cutover_manifest_sha256: str
    target_database_identity: str
    target_alembic_revision: str
    trigger_name: str
    trigger_enabled: bool
    pre_import_tables: tuple[TableVerificationResult, ...]


@dataclass(frozen=True, kw_only=True)
class PostgresRollbackVerificationReport:
    """Typed success or failure report for one read-only target snapshot."""

    report_version: int
    is_match: bool
    source_manifest_sha256: str | None
    cutover_manifest_sha256: str | None
    database_identity: str | None
    alembic_revision: str | None
    trigger_enabled: bool | None
    tables: tuple[TableVerificationResult, ...]
    sequences: tuple[SequenceVerificationResult, ...]
    aggregates: AggregateVerificationResult | None
    mismatches: tuple[RollbackVerificationMismatch, ...]


class PostgresRollbackVerificationError(ValueError):
    """Fail-closed rollback verification error carrying its typed report."""

    def __init__(self, report: PostgresRollbackVerificationReport) -> None:
        """Create an error from a report containing a concrete mismatch.

        Args:
            report: Failed verification report.
        """
        self.report = report
        first = report.mismatches[0]
        super().__init__(
            f"PostgreSQL rollback mismatch at {first.scope}.{first.subject}"
        )
