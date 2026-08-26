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

_TOTALS_SQL = """WITH cohort AS (
    SELECT id, ml_gate_result FROM jobs
    WHERE (? IS NULL OR discovered_at>=?) AND discovered_at<=?
)
SELECT
    (SELECT COUNT(*) FROM cohort) AS total_jobs,
    (SELECT COUNT(*) FROM cohort WHERE ml_gate_result='pass') AS gate_passed,
    (SELECT COUNT(*) FROM evaluations e JOIN cohort c ON c.id=e.job_id
        WHERE e.stage_a_status='completed') AS evaluated,
    (SELECT COUNT(*) FROM evaluations e JOIN cohort c ON c.id=e.job_id
        WHERE e.stage_b_status='completed') AS detailed_reviewed,
    (SELECT COUNT(*) FROM job_status s JOIN cohort c ON c.id=s.job_id
        WHERE s.status IN
          ('applied','interviewing','offer','rejected','ghosted')) AS applied"""
_VERDICTS_SQL = """SELECT
    CASE WHEN stage_b_verdict IS NOT NULL THEN stage_b_verdict
         ELSE 'below_threshold' END AS bucket,
    COUNT(*) AS n
    FROM evaluations e JOIN jobs j ON j.id=e.job_id
    WHERE (? IS NULL OR j.discovered_at>=?) AND j.discovered_at<=?
      AND (stage_b_verdict IS NOT NULL
       OR stage_b_status='skipped_below_threshold'
    )
    GROUP BY bucket"""
_DECISIONS_SQL = """SELECT
    CASE
      WHEN s.status IN ('new','scored') THEN 'results'
      WHEN s.status IN ('shortlisted','awaiting_referral') THEN 'wait'
      WHEN s.status IN ('applied','interviewing','offer','rejected','ghosted')
        THEN 'applied'
      WHEN s.status IN ('ignored','archived') THEN 'ignored'
      ELSE NULL
    END AS bucket,
    COUNT(*) AS n
    FROM job_status s JOIN jobs j ON j.id=s.job_id
    WHERE (? IS NULL OR j.discovered_at>=?) AND j.discovered_at<=?
    GROUP BY bucket"""


def _utc_now() -> datetime:
    return datetime.now(UTC)


async def _daily_counts(
    connection: aiosqlite.Connection,
    table: str,
    column: str,
    cutoff: str | None,
    now: str,
) -> list[aiosqlite.Row]:
    """Return daily event counts limited to the discovery-date cohort."""
    if table == "jobs":
        return await _fetch_rows(
            connection,
            f"""SELECT substr({column}, 1, 10) AS day, COUNT(*) AS n
                FROM {table}
                WHERE (? IS NULL OR {column}>=?) AND {column}<=?
                GROUP BY day""",
            (cutoff, cutoff, now),
        )
    transition_filter = (
        " AND measure.to_status='applied'" if table == ("job_status_history") else ""
    )
    return await _fetch_rows(
        connection,
        f"""SELECT substr(measure.{column}, 1, 10) AS day, COUNT(*) AS n
            FROM {table} measure JOIN jobs j ON j.id=measure.job_id
            WHERE (? IS NULL OR j.discovered_at>=?) AND j.discovered_at<=?
              AND (? IS NULL OR measure.{column}>=?)
              AND measure.{column}<=?{transition_filter}
            GROUP BY day""",
        (cutoff, cutoff, now, cutoff, cutoff, now),
    )


async def _daily_applied_counts(
    connection: aiosqlite.Connection,
    cutoff: str | None,
    now: str,
) -> list[aiosqlite.Row]:
    """Bucket the current Applied decision cohort by its latest apply event."""
    return await _fetch_rows(
        connection,
        """WITH current_applied AS (
               SELECT s.job_id,
                      COALESCE(MAX(h.changed_at), s.last_status_change_at) AS event_at
               FROM job_status s
               JOIN jobs j ON j.id=s.job_id
               LEFT JOIN job_status_history h
                 ON h.job_id=s.job_id AND h.to_status='applied'
               WHERE s.status IN
                 ('applied','interviewing','offer','rejected','ghosted')
                 AND (? IS NULL OR j.discovered_at>=?) AND j.discovered_at<=?
               GROUP BY s.job_id, s.last_status_change_at
           )
           SELECT substr(event_at, 1, 10) AS day, COUNT(*) AS n
           FROM current_applied
           WHERE event_at<=?
           GROUP BY day""",
        (cutoff, cutoff, now, now),
    )


def _merge_days(
    discovered: list[aiosqlite.Row],
    evaluated: list[aiosqlite.Row],
    applied: list[aiosqlite.Row],
) -> list[InsightsDay]:
    """Merge three sparse measure series.

    Time complexity: O(n log n) for a linear merge followed by day sorting.

    Returns:
        Non-empty daily buckets sorted ascending, with missing measures zeroed.
    """
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

    async def insights_overview(self, *, window_days: int | None) -> InsightsOverview:
        """Return one discovery-date cohort and its closed UTC daily series."""
        now = _utc_now()
        now_text = _require_utc_timestamp(now)
        cutoff = (
            None
            if window_days is None
            else _require_utc_timestamp(
                now - timedelta(days=window_days), "insights cutoff"
            )
        )
        async with self._lifecycle.connection() as connection:
            cohort_params = (cutoff, cutoff, now_text)
            totals = await _fetch_row(connection, _TOTALS_SQL, cohort_params)
            verdicts = await _fetch_rows(connection, _VERDICTS_SQL, cohort_params)
            decisions = await _fetch_rows(connection, _DECISIONS_SQL, cohort_params)
            discovered = await _daily_counts(
                connection, "jobs", "discovered_at", cutoff, now_text
            )
            evaluated = await _daily_counts(
                connection, "evaluations", "stage_a_at", cutoff, now_text
            )
            applied = await _daily_applied_counts(connection, cutoff, now_text)
        assert totals is not None
        return InsightsOverview(
            window_days=window_days,
            total_jobs=int(totals["total_jobs"]),
            ml_gate_passed_jobs=int(totals["gate_passed"]),
            evaluated_jobs=int(totals["evaluated"]),
            detailed_reviewed_jobs=int(totals["detailed_reviewed"]),
            applied_jobs=int(totals["applied"]),
            verdict_distribution={
                str(row["bucket"]): int(row["n"]) for row in verdicts
            },
            decision_distribution={
                str(row["bucket"]): int(row["n"]) for row in decisions
            },
            daily=_merge_days(discovered, evaluated, applied),
        )
