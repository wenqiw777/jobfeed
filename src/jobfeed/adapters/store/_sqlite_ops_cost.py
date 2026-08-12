"""SQLite state, cost-ledger, and atomic LLM usage persistence."""

from __future__ import annotations

from datetime import UTC, datetime

import aiosqlite

from jobfeed.adapters.store._sqlite_capability_support import (
    _fetch_row,
    _immediate_transaction,
)
from jobfeed.adapters.store._sqlite_values import _datetime_from_text, _utc_text
from jobfeed.adapters.store.sqlite_lifecycle import SqliteLifecycle
from jobfeed.domain.models import CostEntry, LLMUsage


async def _get_state(lifecycle: SqliteLifecycle, key: str) -> str | None:
    async with lifecycle.connection() as connection:
        row = await _fetch_row(
            connection, "SELECT value FROM state WHERE key=?", (key,)
        )
    return str(row["value"]) if row is not None else None


async def _set_state(lifecycle: SqliteLifecycle, key: str, value: str) -> None:
    async with lifecycle.connection() as connection:
        await connection.execute(
            "INSERT INTO state(key,value) VALUES (?,?) ON CONFLICT(key) "
            "DO UPDATE SET value=excluded.value",
            (key, value),
        )


async def _record_cost_public(
    lifecycle: SqliteLifecycle,
    *,
    day: str,
    spent_usd: float,
    calls: int,
) -> None:
    timestamp = _utc_text(_now())
    async with lifecycle.connection() as connection:
        await _record_cost(
            connection,
            day=day,
            spent_usd=spent_usd,
            calls=calls,
            timestamp=timestamp,
        )


async def _get_cost(lifecycle: SqliteLifecycle, day: str) -> CostEntry | None:
    async with lifecycle.connection() as connection:
        row = await _fetch_row(
            connection,
            "SELECT day,spent_usd,calls,last_updated FROM cost_ledger WHERE day=?",
            (day,),
        )
    if row is None:
        return None
    updated = _datetime_from_text(row["last_updated"])
    if updated is None:
        raise ValueError("cost ledger last_updated is NULL")
    return CostEntry(
        day=str(row["day"]),
        spent_usd=float(row["spent_usd"]),
        calls=int(row["calls"]),
        last_updated=updated,
    )


async def _record_llm_usage_with_cost(
    lifecycle: SqliteLifecycle,
    *,
    day: str,
    spent_usd: float,
    usage: LLMUsage,
) -> None:
    timestamp = _utc_text(_now())
    async with (
        lifecycle.connection() as connection,
        _immediate_transaction(connection),
    ):
        await _insert_usage(connection, usage)
        await _record_cost(
            connection,
            day=day,
            spent_usd=spent_usd,
            calls=0,
            timestamp=timestamp,
        )


async def _insert_usage(
    connection: aiosqlite.Connection,
    usage: LLMUsage,
) -> None:
    await connection.execute(
        """INSERT INTO llm_usage (
            model,input_tokens,output_tokens,cost_usd,cached,latency_ms,
            timestamp,job_id,stage,run_id
        ) VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (
            usage.model,
            usage.input_tokens,
            usage.output_tokens,
            usage.cost_usd,
            int(usage.cached),
            usage.latency_ms,
            _utc_text(usage.timestamp),
            int(usage.job_id) if usage.job_id is not None else None,
            usage.stage,
            usage.run_id,
        ),
    )


async def _record_cost(
    connection: aiosqlite.Connection,
    *,
    day: str,
    spent_usd: float,
    calls: int,
    timestamp: str,
) -> None:
    await connection.execute(
        "INSERT INTO cost_ledger(day,spent_usd,calls,last_updated) VALUES (?,?,?,?) "
        "ON CONFLICT(day) DO UPDATE SET "
        "spent_usd=cost_ledger.spent_usd+excluded.spent_usd,"
        "calls=cost_ledger.calls+excluded.calls,last_updated=excluded.last_updated",
        (day, spent_usd, calls, timestamp),
    )


def _now() -> datetime:
    return datetime.now(UTC)
