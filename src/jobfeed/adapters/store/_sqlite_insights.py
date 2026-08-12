"""SQLite insights aggregates."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import aiosqlite

from jobfeed.adapters.store._sqlite_capability_support import (
    _fetch_row,
    _fetch_rows,
    _require_utc_timestamp,
)
from jobfeed.adapters.store.sqlite_lifecycle import SqliteLifecycle
from jobfeed.domain.models_views import InsightsDay, InsightsOverview

_TOTALS_SQL = """SELECT
    (SELECT COUNT(*) FROM jobs) AS total_jobs,
    (SELECT COUNT(*) FROM jobs WHERE ml_gate_result='pass') AS gate_passed,
    (SELECT COUNT(*) FROM evaluations WHERE stage_a_at IS NOT NULL) AS evaluated,
    (SELECT COUNT(*) FROM applied) AS applied"""
_VERDICTS_SQL = """SELECT
    CASE WHEN stage_b_verdict IS NOT NULL THEN stage_b_verdict
         ELSE 'below_threshold' END AS bucket,
    COUNT(*) AS n
    FROM evaluations
    WHERE stage_b_verdict IS NOT NULL
       OR stage_b_status='skipped_below_threshold'
    GROUP BY bucket"""
_STATUSES_SQL = """SELECT status AS bucket, COUNT(*) AS n
    FROM job_status GROUP BY status"""


def _utc_now() -> datetime:
    return datetime.now(UTC)


async def _daily_counts(
    connection: aiosqlite.Connection,
    table: str,
    column: str,
    cutoff: str,
    now: str,
) -> list[aiosqlite.Row]:
    return await _fetch_rows(
        connection,
        f"""SELECT substr({column}, 1, 10) AS day, COUNT(*) AS n
            FROM {table}
            WHERE {column}>=? AND {column}<=?
            GROUP BY day""",
        (cutoff, now),
    )


def _merge_days(
    discovered: list[aiosqlite.Row],
    evaluated: list[aiosqlite.Row],
    applied: list[aiosqlite.Row],
) -> list[InsightsDay]:
    by_day: dict[date, dict[str, int]] = {}
    for measure, records in (
        ("discovered", discovered),
        ("evaluated", evaluated),
        ("applied", applied),
    ):
        for row in records:
            day = date.fromisoformat(str(row["day"]))
            by_day.setdefault(day, {})[measure] = int(row["n"])
    return [
        InsightsDay(
            day=day,
            discovered=counts.get("discovered", 0),
            evaluated=counts.get("evaluated", 0),
            applied=counts.get("applied", 0),
        )
        for day, counts in sorted(by_day.items())
    ]


class _SqliteInsights:
    """Internal mixin implementing insights aggregation."""

    _lifecycle: SqliteLifecycle

    async def insights_overview(self, *, window_days: int) -> InsightsOverview:
        """Return all-time totals and a closed UTC daily window."""
        now = _utc_now()
        now_text = _require_utc_timestamp(now)
        cutoff = _require_utc_timestamp(
            now - timedelta(days=window_days), "insights cutoff"
        )
        async with self._lifecycle.connection() as connection:
            totals = await _fetch_row(connection, _TOTALS_SQL)
            verdicts = await _fetch_rows(connection, _VERDICTS_SQL)
            statuses = await _fetch_rows(connection, _STATUSES_SQL)
            discovered = await _daily_counts(
                connection, "jobs", "discovered_at", cutoff, now_text
            )
            evaluated = await _daily_counts(
                connection, "evaluations", "stage_a_at", cutoff, now_text
            )
            applied = await _daily_counts(
                connection, "applied", "applied_at", cutoff, now_text
            )
        assert totals is not None
        return InsightsOverview(
            window_days=window_days,
            total_jobs=int(totals["total_jobs"]),
            ml_gate_passed_jobs=int(totals["gate_passed"]),
            evaluated_jobs=int(totals["evaluated"]),
            applied_jobs=int(totals["applied"]),
            verdict_distribution={
                str(row["bucket"]): int(row["n"]) for row in verdicts
            },
            status_distribution={str(row["bucket"]): int(row["n"]) for row in statuses},
            daily=_merge_days(discovered, evaluated, applied),
        )
