"""SQLite company tracking persistence for the operational capability."""

from __future__ import annotations

from datetime import datetime

import aiosqlite

from jobfeed.adapters.store._sqlite_capability_support import _fetch_row, _fetch_rows
from jobfeed.adapters.store._sqlite_values import _datetime_from_text, _utc_text
from jobfeed.adapters.store.sqlite_lifecycle import SqliteLifecycle
from jobfeed.domain.models import CompanyRecord


async def _upsert_company(
    lifecycle: SqliteLifecycle,
    company: CompanyRecord,
) -> None:
    async with lifecycle.connection() as connection:
        await connection.execute(
            """INSERT INTO companies (
                slug,ats_vendor,ats_override,last_verified_at,last_probe_attempt_at,
                job_count_last_scan,notes,consecutive_discover_failures
            ) VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(slug) DO UPDATE SET
                ats_vendor=COALESCE(excluded.ats_vendor,companies.ats_vendor),
                ats_override=excluded.ats_override,
                last_verified_at=COALESCE(
                    excluded.last_verified_at,companies.last_verified_at),
                last_probe_attempt_at=COALESCE(
                    excluded.last_probe_attempt_at,companies.last_probe_attempt_at),
                job_count_last_scan=excluded.job_count_last_scan,
                notes=COALESCE(excluded.notes,companies.notes),
                consecutive_discover_failures=
                    excluded.consecutive_discover_failures""",
            (
                company.slug,
                company.ats_vendor,
                int(company.ats_override),
                _optional_time(company.last_verified_at),
                _optional_time(company.last_probe_attempt_at),
                company.job_count_last_scan,
                company.notes,
                company.consecutive_discover_failures,
            ),
        )


async def _get_company(
    lifecycle: SqliteLifecycle,
    slug: str,
) -> CompanyRecord | None:
    async with lifecycle.connection() as connection:
        row = await _fetch_row(
            connection, "SELECT * FROM companies WHERE slug=?", (slug,)
        )
    return _company_from_row(row) if row is not None else None


async def _list_companies(
    lifecycle: SqliteLifecycle,
    *,
    vendor: str | None,
    include_removed: bool,
) -> list[CompanyRecord]:
    clauses: list[str] = []
    params: list[object] = []
    if not include_removed:
        clauses.append("(ats_vendor IS NULL OR ats_vendor<>'removed')")
    if vendor:
        clauses.append("ats_vendor=?")
        params.append(vendor)
    where = " AND ".join(clauses) if clauses else "TRUE"
    async with lifecycle.connection() as connection:
        rows = await _fetch_rows(
            connection,
            f"SELECT * FROM companies WHERE {where} ORDER BY slug ASC",
            params,
        )
    return [_company_from_row(row) for row in rows]


async def _mark_company_removed(lifecycle: SqliteLifecycle, slug: str) -> bool:
    async with lifecycle.connection() as connection:
        cursor = await connection.execute(
            "UPDATE companies SET ats_vendor='removed',ats_override=0,"
            "last_verified_at=NULL WHERE slug=? AND "
            "(ats_vendor IS NULL OR ats_vendor<>'removed') RETURNING slug",
            (slug,),
        )
        row = await cursor.fetchone()
        await cursor.close()
    return row is not None


async def _bump_discover_failure(lifecycle: SqliteLifecycle, slug: str) -> int:
    async with lifecycle.connection() as connection:
        cursor = await connection.execute(
            "UPDATE companies SET consecutive_discover_failures="
            "consecutive_discover_failures+1 WHERE slug=? "
            "RETURNING consecutive_discover_failures",
            (slug,),
        )
        row = await cursor.fetchone()
        await cursor.close()
    return int(row[0]) if row is not None else 0


async def _reset_discover_failures(lifecycle: SqliteLifecycle, slug: str) -> None:
    async with lifecycle.connection() as connection:
        await connection.execute(
            "UPDATE companies SET consecutive_discover_failures=0 WHERE slug=?",
            (slug,),
        )


def _company_from_row(row: aiosqlite.Row) -> CompanyRecord:
    return CompanyRecord(
        slug=str(row["slug"]),
        ats_vendor=row["ats_vendor"],
        ats_override=bool(row["ats_override"]),
        last_verified_at=_datetime_from_text(row["last_verified_at"]),
        last_probe_attempt_at=_datetime_from_text(row["last_probe_attempt_at"]),
        job_count_last_scan=int(row["job_count_last_scan"]),
        consecutive_discover_failures=int(row["consecutive_discover_failures"]),
        notes=row["notes"],
    )


def _optional_time(value: datetime | None) -> str | None:
    return _utc_text(value) if value is not None else None
