"""Shared transaction, time, and hydration helpers for SQLite capabilities."""

from __future__ import annotations

import asyncio
import sqlite3
import time
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from uuid import UUID

import aiosqlite
import structlog

from jobfeed.domain.models import JobPosting, QualityBand


def _require_utc_timestamp(value: datetime, name: str = "now") -> str:
    """Validate an aware datetime and encode canonical UTC SQLite text."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be an aware datetime")
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _parse_utc_timestamp(value: object) -> datetime:
    """Hydrate canonical SQLite timestamp text as an aware datetime."""
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("persisted SQLite timestamp must be aware")
    return parsed.astimezone(UTC)


def _validate_canonical_uuid(value: str, name: str) -> None:
    """Reject non-UUID or non-canonical UUID identity text."""
    try:
        parsed = UUID(value)
    except (AttributeError, ValueError) as error:
        raise ValueError(f"{name} must be a canonical UUID") from error
    if str(parsed) != value:
        raise ValueError(f"{name} must be a canonical UUID")


def _validate_limit(limit: int) -> None:
    """Reject SQLite's surprising negative-limit unlimited behavior."""
    if isinstance(limit, bool) or limit < 0:
        raise ValueError("limit must be a non-negative integer")


def _placeholders(values: Sequence[object]) -> str:
    """Return one SQLite positional placeholder per non-empty input value."""
    if not values:
        raise ValueError("cannot build placeholders for empty values")
    return ",".join("?" for _ in values)


@asynccontextmanager
async def _immediate_transaction(
    connection: aiosqlite.Connection,
) -> AsyncIterator[None]:
    """Own one short BEGIN IMMEDIATE transaction with rollback on any failure."""
    await _begin_immediate(connection)
    try:
        yield
    except BaseException:
        await connection.rollback()
        raise
    else:
        await connection.commit()


_BUSY_ATTEMPTS = 6
_BUSY_RETRY_DELAY = 0.1
_LOG = structlog.get_logger(__name__)


async def _begin_immediate(connection: aiosqlite.Connection) -> None:
    """Retry only acquisition; never replay a partially executed transaction."""
    started = time.monotonic()
    for attempt in range(_BUSY_ATTEMPTS):
        try:
            await connection.execute("BEGIN IMMEDIATE")
            return
        except asyncio.CancelledError:
            # aiosqlite's queued BEGIN may acquire the lock after cancellation.
            # Queue rollback behind it before returning ownership to the caller.
            await connection.rollback()
            raise
        except sqlite3.OperationalError as exc:
            code = getattr(exc, "sqlite_errorcode", 0)
            if code & 0xFF != sqlite3.SQLITE_BUSY:
                raise
            _LOG.warning(
                "sqlite_writer_busy",
                attempt=attempt + 1,
                elapsed_seconds=round(time.monotonic() - started, 3),
                sqlite_errorname=getattr(exc, "sqlite_errorname", "SQLITE_BUSY"),
            )
            if attempt + 1 == _BUSY_ATTEMPTS:
                raise
            await asyncio.sleep(_BUSY_RETRY_DELAY)


async def _fetch_rows(
    connection: aiosqlite.Connection,
    sql: str,
    params: Sequence[object] = (),
) -> list[aiosqlite.Row]:
    """Fetch mapping rows and close the owned cursor."""
    connection.row_factory = aiosqlite.Row
    cursor = await connection.execute(sql, params)
    try:
        rows = await cursor.fetchall()
    finally:
        await cursor.close()
    return list(rows)


async def _fetch_row(
    connection: aiosqlite.Connection,
    sql: str,
    params: Sequence[object] = (),
) -> aiosqlite.Row | None:
    """Fetch one mapping row and close the owned cursor."""
    connection.row_factory = aiosqlite.Row
    cursor = await connection.execute(sql, params)
    try:
        return await cursor.fetchone()
    finally:
        await cursor.close()


def _hydrate_job(row: aiosqlite.Row) -> JobPosting:
    """Hydrate the public job projection from a SQLite mapping row."""
    quality = row["jd_quality"]
    return JobPosting(
        id=str(row["id"]),
        platform=str(row["platform"]),
        canonical_id=str(row["canonical_id"]),
        url=str(row["url"]),
        title=str(row["title"]),
        company=str(row["company"]),
        location=str(row["location"]),
        discovered_at=_parse_utc_timestamp(row["discovered_at"]),
        jd_text=row["jd_text"],
        jd_quality=QualityBand(str(quality)) if quality is not None else None,
        posted_at=_optional_datetime(row["posted_at"]),
        enriched_at=_optional_datetime(row["enriched_at"]),
        enrich_source=row["enrich_source"],
        closed_at=_optional_datetime(row["closed_at"]),
        enrich_error=row["enrich_error"],
    )


def _optional_datetime(value: object) -> datetime | None:
    return None if value is None else _parse_utc_timestamp(value)
