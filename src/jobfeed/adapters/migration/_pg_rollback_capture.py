"""Read-only PostgreSQL state capture for rollback verification."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from datetime import datetime
from typing import Protocol

from jobfeed.adapters.migration._baseline_evidence_shape import mapping
from jobfeed.adapters.migration._pg_baseline_manifest import (
    aggregate_manifest,
    table_metrics,
)
from jobfeed.adapters.migration._pg_canonical_aggregates import (
    capture_canonical_aggregates,
)
from jobfeed.adapters.migration._pg_rollback_verifier_types import (
    SequenceVerificationResult,
    TableVerificationResult,
)
from jobfeed.adapters.migration.canonical_schema_manifest import (
    CANONICAL_SCHEMA_MANIFEST_V1,
)

_GENERATED_ID_TABLES = (
    "jobs",
    "evaluations",
    "pipeline_runs",
    "job_status_history",
    "llm_usage",
    "interview_rounds",
    "step_timings",
)
_SEQUENCE_SQL = (
    "WITH expected AS (SELECT pg_get_serial_sequence(%s, 'id') AS sequence_name) "
    "SELECT expected.sequence_name, sequences.last_value FROM expected "
    "LEFT JOIN pg_sequences sequences ON "
    "sequences.schemaname || '.' || sequences.sequencename = "
    "expected.sequence_name"
)
_TRIGGER_SQL = (
    "SELECT trigger.tgenabled FROM pg_trigger trigger "
    "JOIN pg_class relation ON relation.oid=trigger.tgrelid "
    "JOIN pg_namespace namespace ON namespace.oid=relation.relnamespace "
    "WHERE namespace.nspname='public' AND relation.relname='jobs' "
    "AND trigger.tgname='trg_jobs_seed_status' AND NOT trigger.tgisinternal"
)


class PostgresRollbackReader(Protocol):
    """Read-only snapshot operations required by the rollback verifier."""

    def scalar(self, sql: str, params: Sequence[object] = ()) -> object:
        """Return one trusted query scalar.

        Args:
            sql: Trusted read-only SQL.
            params: Bound query values.

        Returns:
            First scalar from the query result.
        """
        ...

    def rows(self, sql: str, params: Sequence[object] = ()) -> list[dict[str, object]]:
        """Return trusted query rows as dictionaries.

        Args:
            sql: Trusted read-only SQL.
            params: Bound query values.

        Returns:
            Materialized string-keyed result rows.
        """
        ...

    def stream_table(
        self, table_name: str, chunk_size: int
    ) -> Iterator[dict[str, object]]:
        """Stream one allowlisted table in canonical primary-key order.

        Args:
            table_name: Exact registry table name.
            chunk_size: Positive server-side fetch size.

        Returns:
            Lazy canonical row iterator.
        """
        ...

    def live_schema_document(self) -> dict[str, object]:
        """Return live schema evidence in canonical registry shape.

        Returns:
            Registry-shaped live PostgreSQL schema mapping.
        """
        ...

    def public_base_tables(self) -> list[str]:
        """Return every public PostgreSQL base table.

        Returns:
            Table names in lexical order.
        """
        ...

    def database_identity(self) -> str:
        """Return the non-secret live database identity digest.

        Returns:
            Lowercase SHA-256 identity digest.
        """
        ...


def _capture_table_results(
    reader: PostgresRollbackReader, chunk_size: int
) -> tuple[TableVerificationResult, ...]:
    """Capture canonical metrics for all 14 tables in registry order."""
    metrics = table_metrics(reader, chunk_size)  # type: ignore[arg-type]
    return tuple(
        _table_result(table.name, mapping(metrics[table.name], table.name))
        for table in CANONICAL_SCHEMA_MANIFEST_V1.tables
    )


def _capture_sequence_results(
    reader: PostgresRollbackReader,
    tables: tuple[TableVerificationResult, ...],
) -> tuple[SequenceVerificationResult, ...]:
    """Read every generated-id sequence without advancing it."""
    maxima = {table.table_name: table.max_identity for table in tables}
    results = []
    for table_name in _GENERATED_ID_TABLES:
        rows = reader.rows(_SEQUENCE_SQL, (table_name,))
        if len(rows) != 1:
            raise ValueError(f"sequence lookup mismatch: {table_name}")
        name = rows[0].get("sequence_name")
        value = rows[0].get("last_value")
        if not isinstance(name, str) or type(value) is not int:
            raise ValueError(f"sequence state missing: {table_name}")
        results.append(
            SequenceVerificationResult(
                table_name=table_name,
                sequence_name=name,
                last_value=value,
                max_identity=maxima[table_name],
            )
        )
    return tuple(results)


def _read_trigger_code(reader: PostgresRollbackReader) -> str:
    """Return the exact enablement code for the named jobs seed trigger."""
    rows = reader.rows(_TRIGGER_SQL)
    if len(rows) != 1 or not isinstance(rows[0].get("tgenabled"), str):
        raise ValueError("named jobs seed trigger is missing or duplicated")
    return str(rows[0]["tgenabled"])


def _capture_aggregate_manifest(
    reader: PostgresRollbackReader, as_of: datetime
) -> dict[str, object]:
    return aggregate_manifest(capture_canonical_aggregates(reader, as_of))  # type: ignore[arg-type]


def _table_result(
    table_name: str, document: dict[str, object]
) -> TableVerificationResult:
    return TableVerificationResult(
        table_name=table_name,
        row_count=_integer(document["row_count"], f"{table_name} row count"),
        max_identity=(
            _integer(document["max_identity"], f"{table_name} max identity")
            if document["max_identity"] is not None
            else None
        ),
        canonical_sha256=str(document["canonical_sha256"]),
    )


def _integer(value: object, name: str) -> int:
    if type(value) is not int:
        raise ValueError(f"{name} is not an integer: {value!r}")
    return value
