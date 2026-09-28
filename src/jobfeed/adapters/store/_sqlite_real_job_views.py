"""SQL-backed canonical Triage reads with source posting traceability."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from jobfeed.adapters.store._real_job_hard_filters import (
    SQLITE_CANONICAL_DATE,
    SQLITE_FIRST_SEEN,
    sqlite_hard_filter,
)
from jobfeed.adapters.store._repost_sort import triage_sorts
from jobfeed.adapters.store._sqlite_capability_support import _fetch_row, _fetch_rows
from jobfeed.domain.filtering import HardFilters
from jobfeed.domain.real_job_evaluation import mask_stale_evaluation_row
from jobfeed.domain.user_decisions import statuses_for_decision

_POLICY_CTE = "WITH policy(a,b) AS (SELECT json(?),json(?)) "
_BASE = (
    " FROM real_jobs r JOIN jobs j ON j.id=r.representative_job_id "
    "AND j.real_job_id=r.id "
    "JOIN real_job_status s ON s.real_job_id=r.id "
    "LEFT JOIN real_job_evaluations e ON e.real_job_id=r.id "
    "CROSS JOIN policy p "
)
_A_VISIBLE = (
    "(p.a IS NULL OR json_extract(e.input_facts_json,'$.stage_a_policy') IS NULL OR "
    "json_extract(e.input_facts_json,'$.stage_a_policy')=p.a)"
)
_B_VISIBLE = (
    f"({_A_VISIBLE} AND (p.b IS NULL OR "
    "json_extract(e.input_facts_json,'$.stage_b_policy') IS NULL OR "
    "json_extract(e.input_facts_json,'$.stage_b_policy')=p.b))"
)
_A_SCORE = f"CASE WHEN {_A_VISIBLE} THEN e.stage_a_score END"
_B_STATUS = f"CASE WHEN {_B_VISIBLE} THEN e.stage_b_status END"
_B_VERDICT = f"CASE WHEN {_B_VISIBLE} THEN e.stage_b_verdict END"
_FIT = (
    f"CASE WHEN {_B_VISIBLE} THEN "
    "CAST(json_extract(e.stage_b_json,'$.fit_analysis.score') AS INTEGER) END"
)
_SCORE = f"COALESCE({_FIT},{_A_SCORE})"
_PENDING = (
    "CASE WHEN e.stage_a_status='completed' AND NOT " + _A_VISIBLE + " "
    "THEN 'stage_a_policy_changed' "
    "WHEN e.stage_b_status='completed' AND NOT " + _B_VISIBLE + " "
    "THEN 'stage_b_policy_changed' END"
)
_SORTS = triage_sorts(job="j", score=_SCORE, date_expr=SQLITE_CANONICAL_DATE)
_SORTS["discovered_desc"] = f"{SQLITE_FIRST_SEEN} DESC,r.id DESC"
_MAX_LIMIT = 10_000
_SOURCE_BASE = (
    " FROM jobs j LEFT JOIN real_jobs r ON r.id=j.real_job_id "
    "LEFT JOIN real_job_status s ON s.real_job_id=r.id "
    "LEFT JOIN job_status js ON js.job_id=j.id "
    "LEFT JOIN real_job_evaluations e ON e.real_job_id=r.id "
    "CROSS JOIN policy p "
)
_LIBRARY_SORTS = {
    "discovered_desc": "j.discovered_at DESC,j.id DESC",
    "posted_asc": "COALESCE(j.posted_at,j.discovered_at) ASC,j.id DESC",
    "posted_desc": "COALESCE(j.posted_at,j.discovered_at) DESC,j.id DESC",
    "score_asc": f"{_SCORE} IS NULL,{_SCORE} ASC,j.id DESC",
    "score_desc": f"{_SCORE} IS NULL,{_SCORE} DESC,j.id DESC",
    "company_asc": "j.company_norm ASC,j.id DESC",
}


def _policy_args(
    stage_a_policy: dict[str, object] | None,
    stage_b_policy: dict[str, object] | None,
) -> tuple[str | None, str | None]:
    """Serialize optional policy bind values for one read transaction."""
    return (
        json.dumps(stage_a_policy, sort_keys=True, separators=(",", ":"))
        if stage_a_policy is not None
        else None,
        json.dumps(stage_b_policy, sort_keys=True, separators=(",", ":"))
        if stage_b_policy is not None
        else None,
    )


def _real_job_predicate(
    decision: str,
    search: str | None,
    require_verdict: bool,
    hard_filters: HardFilters | None,
    now: datetime,
) -> tuple[str, tuple[object, ...], str, tuple[object, ...], str, tuple[object, ...]]:
    statuses = statuses_for_decision(decision)
    shared: list[str] = []
    shared_args: list[object] = []
    if search:
        escaped = search.casefold().replace("\\", "\\\\").replace("%", "\\%")
        escaped = escaped.replace("_", "\\_")
        shared.append(
            "(unicode_casefold(j.company) LIKE ? ESCAPE '\\' OR "
            "unicode_casefold(j.title) LIKE ? ESCAPE '\\')"
        )
        shared_args.extend((f"%{escaped}%", f"%{escaped}%"))
    if require_verdict:
        # Bound expensive source/date reads before applying exact visibility rules.
        shared.append(
            "r.id IN (SELECT real_job_id FROM real_job_evaluations "
            "UNION SELECT id FROM real_jobs WHERE identity_review_state!='clear')"
        )
        shared.append(
            f"({_B_VERDICT} IS NOT NULL OR {_PENDING} IS NOT NULL "
            "OR r.identity_review_state!='clear')"
        )
    shared_where = " AND ".join(shared) if shared else "1=1"
    hard_where, hard_args = sqlite_hard_filter(hard_filters, now)
    placeholders = ",".join("?" for _ in statuses)
    where = f"s.status IN ({placeholders}) AND {shared_where}"
    if decision == "results":
        where += f" AND r.official_closed_at IS NULL AND ({hard_where})"
    args = (*statuses, *shared_args, *(hard_args if decision == "results" else ()))
    return where, args, shared_where, tuple(shared_args), hard_where, tuple(hard_args)


class SqliteRealJobViews:
    """Use real-job IDs for exact counts, sorting, pagination, and detail."""

    async def query_real_jobs_view(  # noqa: PLR0913 - public filter contract
        self,
        *,
        decision: str,
        sort: str = "triage_posted_desc",
        search: str | None = None,
        require_verdict: bool = False,
        limit: int = 25,
        offset: int = 0,
        hard_filters: HardFilters | None = None,
        now: datetime | None = None,
        stage_a_policy: dict[str, object] | None = None,
        stage_b_policy: dict[str, object] | None = None,
    ) -> dict[str, object]:
        """Read a filtered canonical list page and exact decision counts.

        Args:
            decision: Active Triage decision tab.
            sort: Validated SQL ordering for the current view.
            search: Optional escaped company or title search text.
            require_verdict: Require a verdict or an identity-review hold.
            limit: Maximum number of rows to return.
            offset: Number of sorted rows skipped for list pagination.
            hard_filters: Current company, location, and posting-age policy.
            now: Optional fixed clock for reproducible age filtering.
            stage_a_policy: Current Stage A policy used to suppress stale quick scores.
            stage_b_policy: Current Stage B policy for stale-score suppression.

        Returns:
            Canonical cards, exact total, and counts for all decision tabs.

        Raises:
            ValueError: Decision, sort, or page window is invalid.
        """
        statuses_for_decision(decision)
        if sort not in _SORTS or limit < 0 or offset < 0 or limit > _MAX_LIMIT:
            raise ValueError("invalid real-job view window or sort")
        where, page_args, shared_where, shared_args, hard_where, hard_args = (
            _real_job_predicate(
                decision,
                search,
                require_verdict,
                hard_filters,
                now or datetime.now(UTC),
            )
        )
        async with self._lifecycle.connection() as db:
            policy_args = _policy_args(stage_a_policy, stage_b_policy)
            rows = await _fetch_rows(
                db,
                f"{_POLICY_CTE}SELECT r.id AS real_job_id,j.id AS source_job_id,"
                "j.company,j.title,"
                "j.location,j.platform,j.url,"
                f"{SQLITE_CANONICAL_DATE} AS posted_at,"
                f"{SQLITE_FIRST_SEEN} AS discovered_at,"
                "r.official_closed_at AS closed_at,j.jd_quality,"
                "j.company_norm,j.title_norm,"
                "j.is_repost,j.repost_evidence,j.repost_observed_at,"
                f"r.identity_review_state,s.status,{_A_SCORE} AS stage_a_score,"
                f"{_B_STATUS} AS stage_b_status,{_B_VERDICT} AS stage_b_verdict,"
                f"{_PENDING} AS evaluation_stale_reason,"
                f"{_FIT} AS fit_score,"
                f"{_SCORE} AS score {_BASE} WHERE {where} "
                f"ORDER BY {_SORTS[sort]} LIMIT ? OFFSET ?",
                (*policy_args, *page_args, limit, offset),
            )
            count_rows = await _fetch_rows(
                db,
                f"{_POLICY_CTE}SELECT s.status,r.official_closed_at IS NULL AS is_open,"
                f"COUNT(*) AS n {_BASE} WHERE {shared_where} "
                "GROUP BY s.status,is_open",
                (*policy_args, *shared_args),
            )
            result_statuses = statuses_for_decision("results")
            result_placeholders = ",".join("?" for _ in result_statuses)
            result_count = await _fetch_row(
                db,
                f"{_POLICY_CTE}SELECT COUNT(*) AS n {_BASE} "
                f"WHERE s.status IN ({result_placeholders}) AND {shared_where} "
                f"AND r.official_closed_at IS NULL AND ({hard_where})",
                (*policy_args, *result_statuses, *shared_args, *hard_args),
            )
        by_status = {
            name: (
                int(result_count["n"])
                if name == "results"
                else sum(
                    int(row["n"])
                    for row in count_rows
                    if row["status"] in statuses_for_decision(name)
                )
            )
            for name in ("results", "wait", "applied", "ignored")
        }
        return {
            "jobs": [dict(row) for row in rows],
            "total": by_status[decision],
            "tab_counts": by_status,
        }

    async def select_real_job_ids(  # noqa: PLR0913 - same filters as list
        self,
        *,
        decision: str,
        sort: str = "triage_posted_desc",
        search: str | None = None,
        require_verdict: bool = False,
        hard_filters: HardFilters | None = None,
        now: datetime | None = None,
        stage_a_policy: dict[str, object] | None = None,
        stage_b_policy: dict[str, object] | None = None,
    ) -> dict[str, object]:
        """Capture every matching parent ID from one read transaction.

        Args:
            decision: Active Triage decision tab.
            sort: Validated SQL ordering for the current view.
            search: Optional escaped company or title search text.
            require_verdict: Require a verdict or an identity-review hold.
            hard_filters: Current company, location, and posting-age policy.
            now: Optional fixed clock for reproducible age filtering.
            stage_a_policy: Current Stage A policy used to suppress stale quick scores.
            stage_b_policy: Current Stage B policy for stale-score suppression.

        Returns:
            One snapshot of matching parent IDs and its exact total.

        Raises:
            ValueError: The sort or decision is invalid.
        """
        if sort not in _SORTS:
            raise ValueError("invalid real-job view sort")
        where, args, *_ = _real_job_predicate(
            decision,
            search,
            require_verdict,
            hard_filters,
            now or datetime.now(UTC),
        )
        async with self._lifecycle.connection() as db:
            await db.execute("BEGIN")
            try:
                rows = await _fetch_rows(
                    db,
                    f"{_POLICY_CTE}SELECT r.id AS real_job_id {_BASE} WHERE {where} "
                    f"ORDER BY {_SORTS[sort]}",
                    (*_policy_args(stage_a_policy, stage_b_policy), *args),
                )
            finally:
                await db.rollback()
        ids = [str(row["real_job_id"]) for row in rows]
        return {"real_job_ids": ids, "total": len(ids)}

    async def get_real_job_view(
        self,
        real_job_id: str,
        *,
        stage_a_policy: dict[str, object] | None = None,
        stage_b_policy: dict[str, object] | None = None,
    ) -> dict[str, object] | None:
        """Read a canonical parent with its representative posting and source evidence.

        Args:
            real_job_id: Canonical parent ID, never a source posting ID.
            stage_a_policy: Current Stage A policy used to suppress stale quick scores.
            stage_b_policy: Current Stage B policy for stale-score suppression.

        Returns:
            Representative row, sources, and identifiers; None if absent.
        """
        parent = int(real_job_id)
        async with self._lifecycle.connection() as db:
            row = await _fetch_row(
                db,
                f"{_POLICY_CTE}SELECT r.id AS real_job_id,r.identity_review_state,"
                "r.official_closed_at AS canonical_closed_at,"
                f"{SQLITE_CANONICAL_DATE} AS canonical_posted_at,"
                f"{SQLITE_FIRST_SEEN} AS canonical_discovered_at,j.*,"
                "s.status,s.notes,s.next_followup_at,s.resume_variant,"
                "e.stage_a_status,e.stage_a_score,e.stage_a_one_line,"
                "e.stage_b_status,e.stage_b_verdict,e.stage_b_json,"
                "e.input_facts_json AS eval_input_facts_json "
                f"{_BASE} WHERE r.id=?",
                (*_policy_args(stage_a_policy, stage_b_policy), parent),
            )
            if row is None:
                return None
            sources = await _fetch_rows(
                db,
                "SELECT id AS job_id,platform,url,apply_url,title,"
                "discovered_at,posted_at,closed_at,is_repost "
                "FROM jobs WHERE real_job_id=? ORDER BY id",
                (parent,),
            )
            evidence = await _fetch_rows(
                db,
                "SELECT provider,scope,native_id,evidence_job_id,observed_url "
                "FROM real_job_identifiers WHERE real_job_id=? ORDER BY id",
                (parent,),
            )
        parent_row = dict(row)
        mask_stale_evaluation_row(
            parent_row,
            stage_a_policy=stage_a_policy,
            stage_b_policy=stage_b_policy,
        )
        parent_row["closed_at"] = parent_row.pop("canonical_closed_at")
        parent_row["posted_at"] = parent_row.pop("canonical_posted_at")
        parent_row["discovered_at"] = parent_row.pop("canonical_discovered_at")
        return {
            "row": parent_row,
            "sources": [dict(source) for source in sources],
            "identity_evidence": [dict(item) for item in evidence],
        }

    async def query_source_library(  # noqa: PLR0913 - source view filters
        self,
        *,
        decision: str | None,
        sort: str,
        search: str | None,
        limit: int,
        offset: int,
        stage_a_policy: dict[str, object] | None = None,
        stage_b_policy: dict[str, object] | None = None,
    ) -> dict[str, object]:
        """Keep every source row while reading its parent's current decision.

        Args:
            decision: Active Triage decision tab.
            sort: Validated SQL ordering for the current view.
            search: Optional escaped company or title search text.
            limit: Maximum number of rows to return.
            offset: Number of sorted rows skipped for list pagination.
            stage_a_policy: Current Stage A policy used to suppress stale quick scores.
            stage_b_policy: Current Stage B policy for stale-score suppression.

        Returns:
            Source cards, exact total, and empty tab counts.

        Raises:
            ValueError: The sort, decision, or page window is invalid.
        """
        if sort not in _LIBRARY_SORTS or limit < 0 or offset < 0 or limit > _MAX_LIMIT:
            raise ValueError("invalid source library window or sort")
        fragments: list[str] = []
        args: list[object] = []
        if decision is not None:
            statuses = statuses_for_decision(decision)
            status_placeholders = ",".join("?" for _ in statuses)
            fragments.append(
                f"COALESCE(s.status,js.status,'new') IN ({status_placeholders})"
            )
            args.extend(statuses)
        if search:
            escaped = search.casefold().replace("\\", "\\\\").replace("%", "\\%")
            escaped = escaped.replace("_", "\\_")
            fragments.append(
                "(unicode_casefold(j.company) LIKE ? ESCAPE '\\' OR "
                "unicode_casefold(j.title) LIKE ? ESCAPE '\\')"
            )
            args.extend((f"%{escaped}%", f"%{escaped}%"))
        where = " AND ".join(fragments) if fragments else "1=1"
        policy_args = _policy_args(stage_a_policy, stage_b_policy)
        async with self._lifecycle.connection() as db:
            count = await _fetch_row(
                db,
                f"{_POLICY_CTE}SELECT COUNT(*) AS n {_SOURCE_BASE} WHERE {where}",
                (*policy_args, *args),
            )
            rows = await _fetch_rows(
                db,
                f"{_POLICY_CTE}SELECT r.id AS real_job_id,j.id AS source_job_id,"
                "j.company,j.title,"
                "j.location,j.platform,j.url,j.posted_at,j.discovered_at,"
                "j.closed_at,j.jd_quality,j.company_norm,j.title_norm,"
                "j.is_repost,j.repost_evidence,j.repost_observed_at,"
                "COALESCE(s.status,js.status,'new') AS status,"
                f"{_A_SCORE} AS stage_a_score,{_B_STATUS} AS stage_b_status,"
                f"{_B_VERDICT} AS stage_b_verdict,"
                f"{_PENDING} AS evaluation_stale_reason,"
                f"{_FIT} AS fit_score,"
                f"{_SCORE} AS score {_SOURCE_BASE} WHERE {where} "
                f"ORDER BY {_LIBRARY_SORTS[sort]} LIMIT ? OFFSET ?",
                (*policy_args, *args, limit, offset),
            )
        return {
            "jobs": [dict(row) for row in rows],
            "total": int(count["n"]) if count else 0,
            "tab_counts": {},
        }
