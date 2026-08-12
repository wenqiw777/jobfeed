"""Read-only PostgreSQL primitives for canonical baseline capture."""

from __future__ import annotations

import hashlib
from collections.abc import Iterator, Sequence
from contextlib import AbstractContextManager
from datetime import datetime
from typing import Any

import psycopg2  # type: ignore[import-untyped]

from jobfeed.adapters.migration.canonical_schema_manifest import (
    CANONICAL_SCHEMA_MANIFEST_V1,
)


def _identifier(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _primary_key_order(table_name: str) -> str:
    table = next(
        table
        for table in CANONICAL_SCHEMA_MANIFEST_V1.tables
        if table.name == table_name
    )
    by_name = {column.name: column for column in table.columns}
    return ", ".join(
        (
            f'{_identifier(name)} COLLATE "C"'
            if by_name[name].source_sql_type == "text"
            else _identifier(name)
        )
        for name in table.primary_key
    )


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
        order = _primary_key_order(table.name)
        sql = (
            f"SELECT {', '.join(projections)} FROM {_identifier(table.name)} "
            f"ORDER BY {order}"
        )
        cursor = self._conn().cursor(name=f"baseline_{table.name}")
        cursor.itersize = chunk_size
        try:
            cursor.execute(sql)
            names = tuple(column.name for column in table.columns)
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

    def public_base_tables(self) -> list[str]:
        """Return every public base table in lexical order.

        Returns:
            Complete table names, including Alembic's revision table.
        """
        return [
            str(row["table_name"])
            for row in self.rows(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema='public' AND table_type='BASE TABLE' "
                "ORDER BY table_name"
            )
        ]

    def database_identity(self) -> str:
        """Hash PostgreSQL cluster and database identifiers.

        Returns:
            Non-secret identity digest used to prove distinct restores.

        Raises:
            ValueError: If PostgreSQL does not return exactly one identity row.
        """
        row = self.rows(
            "SELECT current_database() AS database_name, "
            "(SELECT oid::text FROM pg_database WHERE datname=current_database()) "
            "AS database_oid, "
            "(SELECT system_identifier::text FROM pg_control_system()) "
            "AS system_identifier"
        )
        if len(row) != 1:
            raise ValueError("database identity query returned unexpected rows")
        identity = "\0".join(
            str(row[0][key])
            for key in ("database_name", "database_oid", "system_identifier")
        )
        return hashlib.sha256(identity.encode()).hexdigest()

    def stage_a_in_progress_count(self) -> int:
        """Return the exact persisted Stage A in-progress row count.

        Returns:
            Count of evaluation rows currently claimed for Stage A.

        Raises:
            ValueError: If PostgreSQL does not return an integer count.
        """
        value = self.scalar(
            "SELECT COUNT(*) FROM evaluations WHERE stage_a_status='in_progress'"
        )
        if type(value) is not int:
            raise ValueError("Stage A in-progress count is not an integer")
        return value

    def database_clock(self) -> datetime:
        """Read the database wall clock used as a claim verification cutoff.

        Returns:
            Aware PostgreSQL timestamp from ``clock_timestamp``.

        Raises:
            ValueError: If PostgreSQL does not return a timestamp.
        """
        value = self.scalar("SELECT clock_timestamp()")
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise ValueError("database clock did not return an aware timestamp")
        return value

    def stage_a_claimed_ids_since(self, cutoff: datetime) -> list[str]:
        """List persisted Stage A claims updated at or after a cutoff.

        Args:
            cutoff: Database-derived aware timestamp before worker release.

        Returns:
            Job IDs in numeric order, including reclaimed stale rows.
        """
        return [
            str(row["job_id"])
            for row in self.rows(
                "SELECT job_id FROM evaluations "
                "WHERE stage_a_status='in_progress' AND updated_at >= %s "
                "ORDER BY job_id",
                (cutoff,),
            )
        ]
