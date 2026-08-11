"""Read-only PostgreSQL primitives for canonical baseline capture."""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator, Sequence
from contextlib import AbstractContextManager
from typing import Any

import psycopg2  # type: ignore[import-untyped]

from jobfeed.adapters.migration.canonical_schema_manifest import (
    CANONICAL_SCHEMA_MANIFEST_V1,
)


def _identifier(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


class PostgresBaselineReader(AbstractContextManager["PostgresBaselineReader"]):
    """One repeatable-read, read-only PostgreSQL snapshot plus safe benchmarks."""

    def __init__(self, dsn: str) -> None:
        """Remember the DSN without opening a session.

        Args:
            dsn: PostgreSQL connection string supplied through a named env var.
        """
        self._dsn = dsn
        self._connection: Any | None = None

    def __enter__(self) -> PostgresBaselineReader:
        """Open a repeatable-read, read-only transaction.

        Returns:
            Active baseline reader.
        """
        connection = psycopg2.connect(
            self._dsn, application_name="jobfeed-baseline-capture"
        )
        connection.set_session(
            isolation_level="REPEATABLE READ", readonly=True, autocommit=False
        )
        self._connection = connection
        return self

    def __exit__(self, *args: object) -> None:
        """Rollback the read-only snapshot and close its connection."""
        if self._connection is not None:
            self._connection.rollback()
            self._connection.close()
            self._connection = None

    def _conn(self) -> Any:
        if self._connection is None:
            raise RuntimeError("PostgreSQL baseline reader is not open")
        return self._connection

    def scalar(self, sql: str, params: Sequence[object] = ()) -> object:
        """Return the first column of exactly one query row.

        Args:
            sql: Trusted read-only SQL.
            params: Bound query values.

        Returns:
            First scalar value, or None when there is no row.
        """
        with self._conn().cursor() as cursor:
            cursor.execute(sql, params)
            row = cursor.fetchone()
        return row[0] if row else None

    def rows(self, sql: str, params: Sequence[object] = ()) -> list[dict[str, object]]:
        """Return a bounded query as dictionaries.

        Args:
            sql: Trusted read-only SQL.
            params: Bound query values.

        Returns:
            Materialized result rows.
        """
        with self._conn().cursor() as cursor:
            cursor.execute(sql, params)
            names = tuple(column.name for column in cursor.description or ())
            return [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]

    def stream_table(
        self, table_name: str, chunk_size: int
    ) -> Iterator[dict[str, object]]:
        """Stream one registry table in canonical PK order.

        Args:
            table_name: Exact allowlisted manifest table.
            chunk_size: Server-side cursor fetch size.

        Returns:
            Lazy iterator over canonical row dictionaries.

        Yields:
            Row dictionaries in primary-key order.
        """
        table = next(
            table
            for table in CANONICAL_SCHEMA_MANIFEST_V1.tables
            if table.name == table_name
        )
        projections = []
        for column in table.columns:
            identifier = _identifier(column.name)
            projections.append(
                f"{identifier}::text AS {identifier}"
                if column.source_sql_type == "jsonb"
                else identifier
            )
        order = ", ".join(_identifier(name) for name in table.primary_key)
        sql = (
            f"SELECT {', '.join(projections)} FROM {_identifier(table.name)} "
            f"ORDER BY {order}"
        )
        cursor = self._conn().cursor(name=f"baseline_{table.name}")
        cursor.itersize = chunk_size
        try:
            cursor.execute(sql)
            names = tuple(column.name for column in cursor.description or ())
            for row in cursor:
                yield dict(zip(names, row, strict=True))
        finally:
            cursor.close()

    def live_schema_document(self) -> dict[str, object]:
        """Build a registry-shaped document from live information_schema.

        Time complexity is O(T * C), where T is the fixed registry table count and
        C is the total number of live columns copied into the document.

        Returns:
            Schema document using live order/type/nullability/PK evidence.
        """
        tables: list[dict[str, object]] = []
        for expected in CANONICAL_SCHEMA_MANIFEST_V1.tables:
            columns = self.rows(
                "SELECT column_name, data_type, is_nullable "
                "FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name=%s "
                "ORDER BY ordinal_position",
                (expected.name,),
            )
            primary_key = self.rows(
                "SELECT a.attname AS column_name FROM pg_index i "
                "JOIN pg_class c ON c.oid=i.indrelid "
                "JOIN pg_namespace n ON n.oid=c.relnamespace "
                "JOIN unnest(i.indkey) WITH ORDINALITY k(attnum, ord) ON TRUE "
                "JOIN pg_attribute a ON a.attrelid=c.oid AND a.attnum=k.attnum "
                "WHERE n.nspname='public' AND c.relname=%s AND i.indisprimary "
                "ORDER BY k.ord",
                (expected.name,),
            )
            expected_by_name = {column.name: column for column in expected.columns}
            mapped_columns = []
            for live in columns:
                name = str(live["column_name"])
                reference = expected_by_name.get(name)
                mapped_columns.append(
                    {
                        "name": name,
                        "source_sql_type": str(live["data_type"]),
                        "target_sqlite_type": (
                            reference.target_sqlite_type if reference else "UNKNOWN"
                        ),
                        "codec_kind": reference.codec_kind if reference else "unknown",
                        "nullable": live["is_nullable"] == "YES",
                    }
                )
            tables.append(
                {
                    "name": expected.name,
                    "primary_key": [str(row["column_name"]) for row in primary_key],
                    "columns": mapped_columns,
                }
            )
        return {
            "manifest_version": 1,
            "canonical_row_codec_version": "jobfeed-canonical-row-v1",
            "alembic_revision": "0008",
            "tables": tables,
        }

    def contention_samples(
        self, lock_key: int, hold_ms: int, samples: int
    ) -> list[float]:
        """Measure two-client advisory-lock waiting without mutating source rows.

        Args:
            lock_key: Dedicated PostgreSQL advisory lock key.
            hold_ms: First client's lock hold duration.
            samples: Number of two-client trials.

        Returns:
            Second-client wait durations in milliseconds.
        """
        return [self._contention_once(lock_key, hold_ms) for _ in range(samples)]

    def _contention_once(self, lock_key: int, hold_ms: int) -> float:
        acquired = threading.Event()

        def holder() -> None:
            with (
                psycopg2.connect(self._dsn) as connection,
                connection.cursor() as cursor,
            ):
                cursor.execute("SELECT pg_advisory_xact_lock(%s)", (lock_key,))
                acquired.set()
                time.sleep(hold_ms / 1000)

        thread = threading.Thread(target=holder, daemon=True)
        thread.start()
        if not acquired.wait(timeout=5):
            raise RuntimeError("contention holder did not acquire advisory lock")
        started = time.perf_counter_ns()
        with (
            psycopg2.connect(self._dsn) as connection,
            connection.cursor() as cursor,
        ):
            cursor.execute("SELECT pg_advisory_xact_lock(%s)", (lock_key,))
        elapsed_ms = (time.perf_counter_ns() - started) / 1_000_000
        thread.join(timeout=5)
        if thread.is_alive():
            raise RuntimeError("contention holder did not exit")
        return elapsed_ms
