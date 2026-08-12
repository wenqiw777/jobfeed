"""PostgreSQL-0008 schema, trigger, writer, and sequence controls."""

from __future__ import annotations

from typing import Any, Final, cast

from jobfeed.adapters.migration._pg_rollback_types import PostgresRollbackError
from jobfeed.adapters.migration.canonical_schema_manifest import (
    CANONICAL_SCHEMA_MANIFEST_V1,
    canonical_schema_manifest_document,
    validate_schema_manifest,
)

_EXPECTED_PUBLIC_TABLES: Final = {
    *(table.name for table in CANONICAL_SCHEMA_MANIFEST_V1.tables),
    "alembic_version",
}
_TRIGGER_NAME: Final = "trg_jobs_seed_status"
_AFTER_INSERT_ROW_TRIGGER_TYPE: Final = 5


async def _validate_target_schema(connection: Any) -> str:
    revision = await connection.fetchval("SELECT version_num FROM alembic_version")
    if revision != "0008":
        raise PostgresRollbackError(
            f"rollback target requires revision 0008, got {revision!r}"
        )
    tables = await connection.fetch(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema='public' AND table_type='BASE TABLE'"
    )
    actual = {str(row["table_name"]) for row in tables}
    if actual != _EXPECTED_PUBLIC_TABLES:
        raise PostgresRollbackError(
            "rollback target public table mismatch: "
            f"missing={sorted(_EXPECTED_PUBLIC_TABLES - actual)}, "
            f"extra={sorted(actual - _EXPECTED_PUBLIC_TABLES)}"
        )
    try:
        validate_schema_manifest(await _live_schema_document(connection))
    except ValueError as error:
        raise PostgresRollbackError(str(error)) from error
    return str(revision)


async def _live_schema_document(connection: Any) -> dict[str, object]:
    """Build registry-shaped live DDL. Complexity is O(tables * columns)."""
    expected = canonical_schema_manifest_document()
    tables = []
    expected_tables = cast(list[dict[str, object]], expected["tables"])
    for expected_table in expected_tables:
        name = str(expected_table["name"])
        columns = await connection.fetch(
            "SELECT column_name,data_type,is_nullable "
            "FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name=$1 ORDER BY ordinal_position",
            name,
        )
        primary_key = await connection.fetch(
            "SELECT a.attname AS column_name FROM pg_index i "
            "JOIN pg_class c ON c.oid=i.indrelid "
            "JOIN pg_namespace n ON n.oid=c.relnamespace "
            "JOIN unnest(i.indkey) WITH ORDINALITY k(attnum,ord) ON TRUE "
            "JOIN pg_attribute a ON a.attrelid=c.oid AND a.attnum=k.attnum "
            "WHERE n.nspname='public' AND c.relname=$1 AND i.indisprimary "
            "ORDER BY k.ord",
            name,
        )
        raw_columns = cast(list[dict[str, object]], expected_table["columns"])
        expected_columns = {str(column["name"]): column for column in raw_columns}
        tables.append(
            {
                "name": name,
                "primary_key": [str(row["column_name"]) for row in primary_key],
                "columns": [_live_column(row, expected_columns) for row in columns],
            }
        )
    return {**expected, "tables": tables}


def _live_column(
    row: Any, expected_columns: dict[str, dict[str, object]]
) -> dict[str, object]:
    name = str(row["column_name"])
    reference = expected_columns.get(name)
    return {
        "name": name,
        "source_sql_type": str(row["data_type"]),
        "target_sqlite_type": (
            reference["target_sqlite_type"] if reference else "UNKNOWN"
        ),
        "codec_kind": reference["codec_kind"] if reference else "unknown",
        "nullable": row["is_nullable"] == "YES",
    }


async def _require_quiescent_target(connection: Any) -> None:
    active_writers = await connection.fetchval(
        "SELECT COUNT(*) FROM pg_stat_activity "
        "WHERE datname=current_database() AND pid<>pg_backend_pid() "
        "AND backend_type='client backend' AND state<>'idle'"
    )
    running_runs = await connection.fetchval(
        "SELECT COUNT(*) FROM pipeline_runs WHERE status='running'"
    )
    if active_writers or running_runs:
        raise PostgresRollbackError(
            "rollback target is not quiescent: "
            f"active_writers={active_writers}, running_runs={running_runs}"
        )


async def _lock_target_tables(connection: Any) -> None:
    names = ",".join(f'"{table.name}"' for table in CANONICAL_SCHEMA_MANIFEST_V1.tables)
    await connection.execute(f"LOCK TABLE {names} IN ACCESS EXCLUSIVE MODE")


async def _trigger_is_enabled(connection: Any) -> bool:
    rows = await connection.fetch(
        "SELECT tgenabled,tgfoid::regprocedure::text AS function_name,tgtype "
        "FROM pg_trigger "
        "WHERE tgrelid='jobs'::regclass AND tgname=$1 AND NOT tgisinternal",
        _TRIGGER_NAME,
    )
    if len(rows) != 1:
        raise PostgresRollbackError(
            "rollback target exact jobs seed trigger is missing"
        )
    if (
        rows[0]["function_name"] != "trg_jobs_seed_status_fn()"
        or rows[0]["tgtype"] != _AFTER_INSERT_ROW_TRIGGER_TYPE
    ):
        raise PostgresRollbackError(
            "rollback target jobs seed trigger definition differs"
        )
    raw_enabled = rows[0]["tgenabled"]
    enabled = (
        raw_enabled.decode("ascii")
        if isinstance(raw_enabled, bytes)
        else str(raw_enabled)
    )
    if enabled not in {"O", "D"}:
        raise PostgresRollbackError(
            f"rollback target jobs seed trigger has unsupported state {enabled!r}"
        )
    return enabled == "O"


async def _disable_trigger(connection: Any) -> None:
    await connection.execute(f'ALTER TABLE jobs DISABLE TRIGGER "{_TRIGGER_NAME}"')
    if await _trigger_is_enabled(connection):
        raise PostgresRollbackError("rollback target jobs seed trigger stayed enabled")


async def _enable_trigger(connection: Any) -> None:
    await connection.execute(f'ALTER TABLE jobs ENABLE TRIGGER "{_TRIGGER_NAME}"')
    if not await _trigger_is_enabled(connection):
        raise PostgresRollbackError("rollback target jobs seed trigger stayed disabled")


async def _reset_sequences(connection: Any) -> None:
    for table in CANONICAL_SCHEMA_MANIFEST_V1.tables:
        if not any(column.name == "id" for column in table.columns):
            continue
        sequence = await connection.fetchval(
            "SELECT pg_get_serial_sequence($1, 'id')", table.name
        )
        if not isinstance(sequence, str) or not sequence:
            raise PostgresRollbackError(
                f"rollback target sequence missing for {table.name}.id"
            )
        maximum = await connection.fetchval(f'SELECT MAX(id) FROM "{table.name}"')
        if maximum is not None and type(maximum) is not int:
            raise PostgresRollbackError(
                f"rollback target max identity invalid for {table.name}.id"
            )
        restart_with = 1 if maximum is None else maximum + 1
        # ALTER SEQUENCE RESTART is transactional; setval is not and could survive a
        # replay rollback after a later trigger/parity failure.
        await connection.execute(
            f"ALTER SEQUENCE {_qualified_identifier(sequence)} "
            f"RESTART WITH {restart_with}"
        )


def _qualified_identifier(value: str) -> str:
    return ".".join(
        f'"{part.replace(chr(34), chr(34) * 2)}"' for part in value.split(".")
    )
