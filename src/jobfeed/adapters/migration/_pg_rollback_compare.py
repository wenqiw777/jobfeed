"""Deterministic comparisons and failure reports for rollback parity."""

from __future__ import annotations

from dataclasses import dataclass
from typing import NoReturn

from jobfeed.adapters.migration._pg_rollback_verifier_types import (
    AggregateVerificationResult,
    PostgresRollbackVerificationError,
    PostgresRollbackVerificationReport,
    RollbackVerificationMismatch,
    SequenceVerificationResult,
    TableVerificationResult,
)

AGGREGATE_FIELDS = (
    "as_of_utc",
    "window_days",
    "pending_stage_a",
    "pending_stage_b",
    "needs_attention_sha256",
    "funnel_sha256",
    "daily_cost_sha256",
    "llm_percentiles_sha256",
)


@dataclass(frozen=True, kw_only=True)
class _ReportContext:
    """Known-good evidence available when a later verification gate fails."""

    source_sha: str | None = None
    cutover_sha: str | None = None
    identity: str | None = None
    revision: str | None = None
    trigger_enabled: bool | None = None
    tables: tuple[TableVerificationResult, ...] = ()


def _table_mismatches(
    actual: tuple[TableVerificationResult, ...],
    expected: tuple[TableVerificationResult, ...],
) -> list[RollbackVerificationMismatch]:
    """Compare fixed tables and fields.

    Time complexity is O(table count * metric field count).
    """
    differences = []
    for actual_table, expected_table in zip(actual, expected, strict=True):
        for field in ("row_count", "max_identity", "canonical_sha256"):
            actual_value = getattr(actual_table, field)
            expected_value = getattr(expected_table, field)
            if actual_value != expected_value:
                differences.append(
                    RollbackVerificationMismatch(
                        scope="table",
                        subject=f"{actual_table.table_name}.{field}",
                        expected=expected_value,
                        actual=actual_value,
                    )
                )
    return differences


def _sequence_mismatches(
    sequences: tuple[SequenceVerificationResult, ...],
) -> list[RollbackVerificationMismatch]:
    """Require every generated sequence at or beyond the persisted maximum."""
    return [
        RollbackVerificationMismatch(
            scope="sequence",
            subject=sequence.table_name,
            expected=f">={sequence.max_identity}",
            actual=sequence.last_value,
        )
        for sequence in sequences
        if sequence.max_identity is not None
        and sequence.last_value < sequence.max_identity
    ]


def _aggregate_mismatches(
    actual: AggregateVerificationResult, expected: dict[str, object]
) -> list[RollbackVerificationMismatch]:
    """Compare every source aggregate field in deterministic contract order."""
    return [
        RollbackVerificationMismatch(
            scope="aggregate",
            subject=field,
            expected=expected[field],
            actual=getattr(actual, field),
        )
        for field in AGGREGATE_FIELDS
        if getattr(actual, field) != expected[field]
    ]


def _aggregate_result(document: dict[str, object]) -> AggregateVerificationResult:
    """Convert a validated aggregate manifest to the typed report value."""
    return AggregateVerificationResult(
        as_of_utc=str(document["as_of_utc"]),
        window_days=_integer(document["window_days"]),
        pending_stage_a=_integer(document["pending_stage_a"]),
        pending_stage_b=_integer(document["pending_stage_b"]),
        needs_attention_sha256=str(document["needs_attention_sha256"]),
        funnel_sha256=str(document["funnel_sha256"]),
        daily_cost_sha256=str(document["daily_cost_sha256"]),
        llm_percentiles_sha256=str(document["llm_percentiles_sha256"]),
    )


def _integer(value: object) -> int:
    if type(value) is not int:
        raise ValueError(f"rollback aggregate is not an integer: {value!r}")
    return value


def _raise_failure(
    scope: str,
    subject: str,
    expected: object,
    actual: object,
    *,
    context: _ReportContext | None = None,
) -> NoReturn:
    """Raise one typed fail-closed report with all established evidence."""
    evidence = context if context is not None else _ReportContext()
    mismatch = RollbackVerificationMismatch(
        scope=scope,
        subject=subject,
        expected=expected,
        actual=actual,
    )
    raise PostgresRollbackVerificationError(
        PostgresRollbackVerificationReport(
            report_version=1,
            is_match=False,
            source_manifest_sha256=evidence.source_sha,
            cutover_manifest_sha256=evidence.cutover_sha,
            database_identity=evidence.identity,
            alembic_revision=evidence.revision,
            trigger_enabled=evidence.trigger_enabled,
            tables=evidence.tables,
            sequences=(),
            aggregates=None,
            mismatches=(mismatch,),
        )
    )
