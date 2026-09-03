"""SQLite jobs-view, twin, and pipeline-run query implementation."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

import aiosqlite

from jobfeed.adapters.store._sqlite_capability_support import (
    _fetch_row,
    _fetch_rows,
    _placeholders,
    _require_utc_timestamp,
)
from jobfeed.adapters.store._sqlite_runs import _pipeline_run_from_row
from jobfeed.adapters.store._sqlite_values import _job_from_row
from jobfeed.adapters.store.sqlite_lifecycle import SqliteLifecycle
from jobfeed.domain.models import PipelineRun
from jobfeed.domain.models_views import (
    VALID_TABS,
    JobsViewPage,
    JobsViewQuery,
    JobsViewRow,
    TwinStatusRow,
)
from jobfeed.domain.scoring import MAX_STAGE_RETRIES
from jobfeed.domain.source_attribution import configured_source_counts

_TAB_PREDICATES: dict[str, str] = {
    "queue": (
        "(s.status IN ('new','scored','shortlisted','awaiting_referral')"
        " AND j.closed_at IS NULL)"
    ),
    "pending_jd": (
        "((j.jd_quality IS NULL OR j.jd_quality IN ('missing','abandoned'))"
        " AND e.stage_a_score IS NULL"
        " AND s.status NOT IN ('archived','ignored')"
        " AND j.closed_at IS NULL)"
    ),
    "all": "1",
    "scored": "s.status = 'scored'",
    "shortlisted": "s.status IN ('shortlisted','awaiting_referral')",
    "archived": "s.status IN ('archived','ignored')",
}

_FROM = (
    " FROM jobs AS j"
    " LEFT JOIN evaluations AS e ON e.job_id=j.id"
    " LEFT JOIN job_status AS s ON s.job_id=j.id"
)
_VERDICT_FROM = (
    " FROM evaluations AS e INDEXED BY idx_eval_stage_b_completed"
    " JOIN jobs AS j ON j.id=e.job_id"
    " LEFT JOIN job_status AS s ON s.job_id=j.id"
)
_EXACT_VERDICT_FROM = (
    " FROM evaluations AS e INDEXED BY idx_eval_verdict_job"
    # SQLite may reorder an ordinary INNER JOIN back to the 90k-row status
    # table even with INDEXED BY. CROSS JOIN makes the small verdict corpus the
    # loop driver while preserving the same inner-join result.
    " CROSS JOIN jobs AS j ON j.id=e.job_id"
    # Every job owns a trigger-seeded status row, so this remains semantically
    # equivalent to the general LEFT JOIN while locking the final loop order.
    " CROSS JOIN job_status AS s ON s.job_id=j.id"
)
_COLUMNS = (
    "j.id, j.platform, j.canonical_id, j.url, j.title, j.company,"
    " j.location, j.discovered_at, NULL AS jd_text, j.jd_quality,"
    " j.posted_at, NULL AS enriched_at, NULL AS enrich_source,"
    " j.closed_at, NULL AS enrich_error, j.company_norm, j.title_norm,"
    " s.status AS status, e.stage_a_score, e.stage_b_verdict,"
    " e.stage_b_status,"
    " CAST(json_extract(e.stage_b_fit_json, '$.score_0_100') AS INTEGER)"
    " AS stage_b_fit_score"
)
_SORTS = {
    "discovered_desc": "j.discovered_at DESC, j.id DESC",
    "posted_desc": (
        "COALESCE(j.posted_at, j.discovered_at) DESC, j.discovered_at DESC, j.id DESC"
    ),
    "posted_asc": (
        "COALESCE(j.posted_at, j.discovered_at) ASC, j.discovered_at DESC, j.id DESC"
    ),
    "score_desc": (
        "COALESCE(CAST(json_extract(e.stage_b_fit_json, '$.score_0_100')"
        " AS INTEGER), e.stage_a_score) IS NULL,"
        " COALESCE(CAST(json_extract(e.stage_b_fit_json, '$.score_0_100')"
        " AS INTEGER), e.stage_a_score) DESC, j.discovered_at DESC, j.id DESC"
    ),
    "score_asc": (
        "COALESCE(CAST(json_extract(e.stage_b_fit_json, '$.score_0_100')"
        " AS INTEGER), e.stage_a_score) IS NULL,"
        " COALESCE(CAST(json_extract(e.stage_b_fit_json, '$.score_0_100')"
        " AS INTEGER), e.stage_a_score) ASC, j.discovered_at DESC, j.id DESC"
    ),
    "company_asc": (
        "j.company_norm IS NULL, j.company_norm ASC, j.discovered_at DESC, j.id DESC"
    ),
}


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _literal_like(value: str) -> str:
    return (
        "%" + value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    )


def _shared_filters(
    query: JobsViewQuery,
    now: datetime,
) -> tuple[list[str], list[object]]:
    fragments: list[str] = []
    params: list[object] = []
    if query.statuses:
        fragments.append(f"s.status IN ({_placeholders(query.statuses)})")
        params.extend(query.statuses)
    if query.search:
        params.append(_literal_like(query.search.casefold()))
        fragments.append(
            "(unicode_casefold(j.company) LIKE ? ESCAPE '\\'"
            " OR unicode_casefold(j.title) LIKE ? ESCAPE '\\')"
        )
        params.append(params[-1])
    if query.posted_within_days is not None:
        cutoff = now - timedelta(days=query.posted_within_days)
        fragments.append("j.discovered_at >= ?")
        params.append(_require_utc_timestamp(cutoff, "freshness cutoff"))
    if query.require_verdict:
        fragments.append("e.stage_b_verdict IS NOT NULL")
    return fragments, params


def _where(predicate: str, shared: Sequence[str]) -> str:
    return " AND ".join((predicate, *shared))


def _view_row(row: aiosqlite.Row) -> JobsViewRow:
    fit_score = row["stage_b_fit_score"]
    return JobsViewRow(
        job=_job_from_row(row),
        company_norm=row["company_norm"],
        title_norm=row["title_norm"],
        status=str(row["status"]),
        verdict=row["stage_b_verdict"],
        stage_a_score=row["stage_a_score"],
        stage_b_fit_score=int(fit_score) if fit_score is not None else None,
        stage_b_status=row["stage_b_status"],
    )


def _jobs_view_rows_query(
    query: JobsViewQuery,
    now: datetime,
) -> tuple[str, list[object]]:
    """Build the production bounded rows query for execution or plan evidence."""
    shared, params = _shared_filters(query, now)
    active_where = _where(_TAB_PREDICATES[query.tab], shared)
    if not query.include_counts and query.require_verdict:
        active_where = f"({active_where}) AND e.stage_b_status='completed'"
    if query.require_verdict:
        rows_from = _VERDICT_FROM if not query.include_counts else _EXACT_VERDICT_FROM
    else:
        rows_from = _FROM
    sql = (
        f"SELECT {_COLUMNS}{rows_from} WHERE {active_where}"
        f" ORDER BY {_SORTS[query.sort]} LIMIT ? OFFSET ?"
    )
    return sql, [*params, query.limit, query.offset]


class _SqliteViews:
    """Internal mixin implementing the typed views port."""

    _lifecycle: SqliteLifecycle

    async def query_jobs_view(self, query: JobsViewQuery) -> JobsViewPage:
        """Return a stable SQL-windowed jobs view and same-filter tab counts."""
        now = _utc_now()
        shared, params = _shared_filters(query, now)
        active_where = _where(_TAB_PREDICATES[query.tab], shared)
        shared_where = " AND ".join(shared) if shared else "1"
        rows_sql, rows_params = _jobs_view_rows_query(query, now)
        count_columns = ", ".join(
            f'COALESCE(SUM(CASE WHEN {predicate} THEN 1 ELSE 0 END), 0) AS "{tab}"'
            for tab, predicate in _TAB_PREDICATES.items()
        )
        async with self._lifecycle.connection() as connection:
            row_records = await _fetch_rows(
                connection,
                rows_sql,
                rows_params,
            )
            if not query.include_counts:
                return JobsViewPage(
                    rows=[_view_row(row) for row in row_records],
                    total=len(row_records),
                    tab_counts={},
                    total_is_exact=False,
                )
            total_row = None
            if query.include_total:
                total_row = await _fetch_row(
                    connection,
                    f"SELECT COUNT(*) AS n{_FROM} WHERE {active_where}",
                    params,
                )
            counts = await _fetch_row(
                connection,
                f"SELECT {count_columns}{_FROM} WHERE {shared_where}",
                params,
            )
        assert counts is not None
        return JobsViewPage(
            rows=[_view_row(row) for row in row_records],
            total=(int(total_row["n"]) if total_row is not None else len(row_records)),
            tab_counts={tab: int(counts[tab]) for tab in VALID_TABS},
        )

    async def list_twin_rows_by_status(
        self,
        keys: Sequence[tuple[str, str]],
        *,
        statuses: Sequence[str],
        limit: int,
    ) -> list[JobsViewRow]:
        """Return bounded exact-pair twin rows in the requested statuses."""
        if not keys or not statuses:
            return []
        unique_keys = list(dict.fromkeys(keys))
        key_values = ",".join("(?,?)" for _ in unique_keys)
        params: list[object] = [value for key in unique_keys for value in key]
        params.extend(statuses)
        params.append(limit)
        sql = (
            f"WITH twin_keys(company_norm,title_norm) AS (VALUES {key_values})"
            f" SELECT {_COLUMNS}{_FROM}"
            " JOIN twin_keys AS tk"
            " ON tk.company_norm=j.company_norm AND tk.title_norm=j.title_norm"
            " WHERE 1"
            " AND j.company_norm <> '' AND j.title_norm <> ''"
            f" AND s.status IN ({_placeholders(statuses)})"
            " ORDER BY j.discovered_at DESC, j.id DESC LIMIT ?"
        )
        async with self._lifecycle.connection() as connection:
            records = await _fetch_rows(connection, sql, params)
        return [_view_row(row) for row in records]

    async def list_twin_statuses(self, job_id: str) -> list[TwinStatusRow]:
        """Return exact non-blank twins excluding the source job."""
        async with self._lifecycle.connection() as connection:
            records = await _fetch_rows(
                connection,
                """SELECT twin.id, twin.platform, twin.url, s.status
                   FROM jobs AS source
                   JOIN jobs AS twin
                     ON twin.company_norm=source.company_norm
                    AND twin.title_norm=source.title_norm
                    AND twin.id<>source.id
                   LEFT JOIN job_status AS s ON s.job_id=twin.id
                   WHERE source.id=?
                     AND source.company_norm<>''
                     AND source.title_norm<>''
                   ORDER BY twin.id""",
                (int(job_id),),
            )
        return [
            TwinStatusRow(
                job_id=str(row["id"]),
                platform=str(row["platform"]),
                url=str(row["url"]),
                status=str(row["status"]),
            )
            for row in records
        ]

    async def list_pipeline_runs(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        days: int | None = None,
    ) -> tuple[list[PipelineRun], int]:
        """Return stable newest-first runs plus total before pagination."""
        where = ""
        params: list[object] = []
        if days is not None:
            where = " WHERE started_at>=?"
            params.append(
                _require_utc_timestamp(_utc_now() - timedelta(days=days), "run cutoff")
            )
        async with self._lifecycle.connection() as connection:
            total = await _fetch_row(
                connection,
                f"SELECT COUNT(*) AS n FROM pipeline_runs{where}",
                params,
            )
            records = await _fetch_rows(
                connection,
                f"""SELECT * FROM pipeline_runs{where}
                    ORDER BY started_at DESC, run_id DESC LIMIT ? OFFSET ?""",
                (*params, limit, offset),
            )
        assert total is not None
        return [_pipeline_run_from_row(row) for row in records], int(total["n"])

    async def get_stage_b_run_progress(
        self, run_id: str, started_at: datetime
    ) -> tuple[int, int]:
        """Reconstruct live Stage B progress for a separately owned worker."""
        async with self._lifecycle.connection() as connection:
            row = await _fetch_row(
                connection,
                """SELECT
                    (SELECT COUNT(DISTINCT u.job_id)
                       FROM llm_usage AS u
                       JOIN evaluations AS done ON done.job_id=u.job_id
                      WHERE u.run_id=? AND u.stage='b'
                        AND done.stage_b_status IN ('completed','error')) AS processed,
                    (SELECT COUNT(*) FROM evaluations AS pending
                      WHERE pending.stage_b_status='in_progress'
                        AND pending.stage_b_verdict IS NULL
                        AND pending.updated_at>=?) AS remaining""",
                (run_id, _require_utc_timestamp(started_at)),
            )
        assert row is not None
        processed = int(row["processed"])
        return processed, processed + int(row["remaining"])

    async def get_new_job_source_counts(self, run_id: str) -> dict[str, int]:
        """Count first inserts during a scan, grouped by configured source.

        Args:
            run_id: Historical or active pipeline run identity.

        Returns:
            Exact first-insert counts grouped by configured source.
        """
        async with self._lifecycle.connection() as connection:
            records = await _fetch_rows(
                connection,
                """SELECT j.platform, COUNT(*) AS n
                   FROM pipeline_runs AS r
                   JOIN job_status_history AS h
                     ON h.changed_at >= r.started_at
                    AND h.changed_at <= COALESCE(
                        r.finished_at,
                        strftime('%Y-%m-%dT%H:%M:%f000Z','now')
                    )
                   JOIN jobs AS j ON j.id = h.job_id
                   WHERE r.run_id=?
                     AND r.source <> 'evaluate'
                     AND h.from_status IS NULL
                     AND h.to_status = 'new'
                   GROUP BY j.platform""",
                (run_id,),
            )
        return configured_source_counts(
            (str(row["platform"]), int(row["n"])) for row in records
        )

    async def list_retryable_run_error_job_ids(self, run_id: str) -> list[str]:
        """Return current retryable scoring errors attributable to one run."""
        async with self._lifecycle.connection() as connection:
            records = await _fetch_rows(
                connection,
                f"""SELECT e.job_id
                    FROM pipeline_runs AS r
                    JOIN evaluations AS e
                      ON e.updated_at >= r.started_at
                     AND e.updated_at <= r.finished_at
                    WHERE r.run_id=?
                      AND r.source='evaluate'
                      AND r.finished_at IS NOT NULL
                      AND (
                        (e.stage_a_status='error'
                         AND e.stage_a_error_count < {MAX_STAGE_RETRIES})
                        OR
                        (e.stage_b_status='error'
                         AND e.stage_b_error_count < {MAX_STAGE_RETRIES})
                      )
                    ORDER BY e.updated_at, e.job_id""",
                (run_id,),
            )
        return [str(row["job_id"]) for row in records]
