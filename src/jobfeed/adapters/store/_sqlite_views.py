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
_COLUMNS = (
    "j.*, s.status AS status, e.stage_a_score, e.stage_b_verdict,"
    " e.stage_b_status,"
    " CAST(json_extract(e.stage_b_fit_json, '$.score_0_100') AS INTEGER)"
    " AS stage_b_fit_score"
)
_SORTS = {
    "discovered_desc": "j.discovered_at DESC, j.id DESC",
    "posted_desc": (
        "j.posted_at IS NULL, j.posted_at DESC, j.discovered_at DESC, j.id DESC"
    ),
    "score_desc": (
        "COALESCE(CAST(json_extract(e.stage_b_fit_json, '$.score_0_100')"
        " AS INTEGER), e.stage_a_score) IS NULL,"
        " COALESCE(CAST(json_extract(e.stage_b_fit_json, '$.score_0_100')"
        " AS INTEGER), e.stage_a_score) DESC, j.discovered_at DESC, j.id DESC"
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
    sql = (
        f"SELECT {_COLUMNS}{_FROM} WHERE {active_where}"
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
        assert total_row is not None and counts is not None
        return JobsViewPage(
            rows=[_view_row(row) for row in row_records],
            total=int(total_row["n"]),
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
        key_predicate = " OR ".join(
            "(j.company_norm=? AND j.title_norm=?)" for _ in keys
        )
        params: list[object] = [value for key in keys for value in key]
        params.extend(statuses)
        params.append(limit)
        sql = (
            f"SELECT {_COLUMNS}{_FROM} WHERE ({key_predicate})"
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
