"""PostgreSQL implementation of the JobStore port using asyncpg."""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from typing import Any, cast

try:
    import asyncpg  # type: ignore[import-untyped]
except ImportError as _exc:  # pragma: no cover
    raise ImportError(
        "asyncpg is required for PostgresStore. Install it with: pip install asyncpg"
    ) from _exc

from jobfeed.adapters.store._normalize import normalize, normalize_company
from jobfeed.adapters.store.legacy_import import (
    AppliedRow,
    CompanyRow,
    CostLedgerRow,
    EvaluationRow,
    JobRow,
    JobStatusRow,
    ResumeSnapshotRow,
    ResumeVariantRow,
    StateRow,
    StatusHistoryRow,
)
from jobfeed.domain.errors import SnapshotAmbiguousError, SnapshotNotFoundError
from jobfeed.domain.interview import InterviewRound
from jobfeed.domain.models import (
    ApplicationRecord,
    ApplicationStats,
    AttentionItem,
    AttentionReport,
    AutoDecayResult,
    BulkResult,
    BulkTransitionRequest,
    CompanyRecord,
    CostEntry,
    DigestStats,
    FitAnalysis,
    GapItem,
    JobEvaluation,
    JobPosting,
    MatchItem,
    MLGateResult,
    PipelineRun,
    QualityBand,
    ResumeSnapshot,
    ResumeSnapshotSummary,
    ResumeVariantStats,
    SaveJobResult,
    StageAResult,
    StageBResult,
    StatusFilter,
    StatusInfo,
    TransitionRequest,
    UnenrichedJob,
    Verdict,
    WorkflowAttention,
    WorkflowAttentionItem,
)
from jobfeed.domain.models_llm import LLMUsage
from jobfeed.domain.models_perf import (
    FunnelStats,
    LLMDailyStats,
    PerformanceOverview,
    StepTiming,
    StepTimingSeries,
)
from jobfeed.domain.models_views import (
    VALID_TABS,
    InsightsDay,
    InsightsOverview,
    JobsViewPage,
    JobsViewQuery,
    JobsViewRow,
    TwinStatusRow,
)
from jobfeed.domain.quality import assess_quality, quality_rank
from jobfeed.domain.scoring import MAX_STAGE_RETRIES
from jobfeed.domain.status import (
    ACTIVE_APPLICATION_STATUSES,
    DECAY_SOURCES,
    DEFAULT_ARCHIVE_IGNORED_DAYS,
    DEFAULT_FOLLOWUP_GRACE_DAYS,
    DEFAULT_GHOST_DAYS,
    REASON_AUTO_SCORED,
    RESPONSE_STATUSES,
    is_terminal,
    validate_transition,
)
from jobfeed.domain.types import VALID_SEVERITIES, Severity
from jobfeed.ports.source import StoredEnrichment
from jobfeed.ports.store_claims import GateCandidate

# ---------------------------------------------------------------------------
# Row mapping helpers
# ---------------------------------------------------------------------------


def _escape_like_prefix(prefix: str) -> str:
    """Backslash-escape LIKE wildcards so a user prefix matches literally.

    Args:
        prefix: Raw user-supplied prefix.

    Returns:
        Prefix safe for ``LIKE ... ESCAPE '\\'`` (no wildcard expansion).
    """
    # Escape the escape char first, or the % / _ escapes below get double-escaped.
    return prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _opt_float(value: Any) -> float | None:
    """Coerce an optional numeric database value to ``float | None``.

    Args:
        value: Possibly-None numeric from an asyncpg Record.

    Returns:
        Float when truthy, else None.
    """
    return float(value) if value is not None else None


def _job_from_record(r: asyncpg.Record) -> JobPosting:
    """Build a JobPosting from an asyncpg Record.

    Args:
        r: Database record containing jobs table columns.

    Returns:
        Domain job posting with string identity.
    """
    return JobPosting(
        id=str(r["id"]),
        platform=r["platform"],
        canonical_id=r["canonical_id"],
        url=r["url"],
        title=r["title"],
        company=r["company"],
        location=r["location"],
        discovered_at=r["discovered_at"],
        jd_text=r["jd_text"],
        jd_quality=QualityBand(r["jd_quality"]) if r["jd_quality"] else None,
        posted_at=r["posted_at"],
        enriched_at=r["enriched_at"],
        enrich_source=r["enrich_source"],
        closed_at=r["closed_at"],
        enrich_error=r["enrich_error"],
    )


# ---------------------------------------------------------------------------
# Jobs view (Phase 8 web read path)
# ---------------------------------------------------------------------------

# Tab predicates over jobs LEFT JOIN evaluations LEFT JOIN job_status. Triage
# tabs (queue, pending_jd) exclude closed rows; Library tabs include them.
# job_status rows are trigger-seeded on every jobs insert, so
# job_status.status is never NULL despite the LEFT JOIN.
_JOBS_VIEW_TAB_PREDICATES: dict[str, str] = {
    "queue": (
        "(job_status.status IN"
        " ('new', 'scored', 'shortlisted', 'awaiting_referral')"
        " AND jobs.closed_at IS NULL)"
    ),
    # Pending-JD: no usable JD (same quality predicate as
    # mark_stale_jobs_closed), never Stage A-scored, not archived/ignored,
    # still open (rewrite-spec §15 semantics).
    "pending_jd": (
        "((jobs.jd_quality IS NULL"
        " OR jobs.jd_quality IN ('missing', 'abandoned'))"
        " AND evaluations.stage_a_score IS NULL"
        " AND job_status.status NOT IN ('archived', 'ignored')"
        " AND jobs.closed_at IS NULL)"
    ),
    "all": "TRUE",
    "scored": "job_status.status = 'scored'",
    "shortlisted": "job_status.status IN ('shortlisted', 'awaiting_referral')",
    "archived": "job_status.status IN ('archived', 'ignored')",
}

_JOBS_VIEW_FROM = (
    "FROM jobs"
    " LEFT JOIN evaluations ON evaluations.job_id = jobs.id"
    " LEFT JOIN job_status ON job_status.job_id = jobs.id"
)

_JOBS_VIEW_COLUMNS = (
    "jobs.*, job_status.status AS status,"
    " evaluations.stage_a_score, evaluations.stage_b_verdict,"
    " evaluations.stage_b_status,"
    " (evaluations.stage_b_fit_json ->> 'score_0_100')::integer"
    " AS stage_b_fit_score"
)

# SQL ORDER BY per JobsViewQuery.sort, mirroring the in-memory keys in
# services/_jobs_view_sort.py (score = Stage B fit with Stage A fallback,
# NULLS LAST; every sort ends in the discovered_at DESC, id DESC tiebreak)
# so plain Library requests paginate in SQL beyond the corpus cap (D10).
_JOBS_VIEW_SORT_SQL: dict[str, str] = {
    "discovered_desc": "jobs.discovered_at DESC, jobs.id DESC",
    "posted_desc": (
        "jobs.posted_at DESC NULLS LAST, jobs.discovered_at DESC, jobs.id DESC"
    ),
    "score_desc": (
        "COALESCE((evaluations.stage_b_fit_json ->> 'score_0_100')::integer,"
        " evaluations.stage_a_score) DESC NULLS LAST,"
        " jobs.discovered_at DESC, jobs.id DESC"
    ),
    "company_asc": (
        "jobs.company_norm ASC NULLS LAST, jobs.discovered_at DESC, jobs.id DESC"
    ),
}


def _jobs_view_filters(query: JobsViewQuery) -> tuple[list[str], list[object]]:
    """Build the request's shared WHERE fragments and their $1-based params.

    These filters (statuses, search, freshness, require_verdict) apply to the
    active tab's rows and total AND to every tab_counts bucket, so
    tab_counts[t] always equals the total of re-running the request with
    tab=t.

    Args:
        query: Jobs view query.

    Returns:
        Tuple of (SQL fragments, positional params), params numbered $1..$n.
    """
    fragments: list[str] = []
    params: list[object] = []
    if query.statuses:
        params.append(list(query.statuses))
        fragments.append(f"job_status.status = ANY(${len(params)}::text[])")
    if query.search:
        params.append(f"%{_escape_like_prefix(query.search)}%")
        index = len(params)
        fragments.append(f"(jobs.company ILIKE ${index} OR jobs.title ILIKE ${index})")
    if query.posted_within_days is not None:
        # Freshness cuts on discovered_at per rewrite-spec §15 — a job posted
        # long ago but newly scraped is fresh to the user's workflow.
        params.append(query.posted_within_days)
        fragments.append(
            f"jobs.discovered_at >= now() - make_interval(days => ${len(params)})"
        )
    if query.require_verdict:
        fragments.append("evaluations.stage_b_verdict IS NOT NULL")
    return fragments, params


def _jobs_view_counts_sql(shared_where: str) -> str:
    """Render the one-query per-tab COUNT(*) FILTER aggregate.

    Args:
        shared_where: WHERE clause of shared request filters ('TRUE' if none).

    Returns:
        SQL returning one row with a count column per tab. Aliases are quoted
        because 'all' is a reserved word.
    """
    selects = ", ".join(
        f'COUNT(*) FILTER (WHERE {_JOBS_VIEW_TAB_PREDICATES[tab]}) AS "{tab}"'
        for tab in VALID_TABS
    )
    return f"SELECT {selects} {_JOBS_VIEW_FROM} WHERE {shared_where}"


def _jobs_view_row_from_record(r: asyncpg.Record) -> JobsViewRow:
    """Build a JobsViewRow from a jobs+evaluations+job_status record.

    Args:
        r: Joined record carrying jobs.*, the status alias, and the
            evaluation summary columns.

    Returns:
        View row. The Stage B fit score is lifted from stage_b_fit_json's
        'score_0_100' key — the same key get_evaluation hydrates FitAnalysis
        from.
    """
    return JobsViewRow(
        job=_job_from_record(r),
        company_norm=r["company_norm"],
        title_norm=r["title_norm"],
        status=r["status"],
        verdict=r["stage_b_verdict"],
        stage_a_score=r["stage_a_score"],
        stage_b_fit_score=r["stage_b_fit_score"],
        stage_b_status=r["stage_b_status"],
    )


def _gate_candidate_from_record(r: asyncpg.Record) -> GateCandidate:
    """Build a GateCandidate (job + persisted ml_gate_result) from a jobs record.

    Reuses ``_job_from_record`` for identity/columns and lifts the persisted
    ``jobs.ml_gate_result`` (already present via ``SELECT jobs.*``) so the funnel
    can skip re-gating an already-'pass' representative.

    Args:
        r: ``jobs`` table record (must include the ``ml_gate_result`` column).

    Returns:
        Gate candidate carrying the job and its stored gate verdict (or None).
    """
    return GateCandidate(
        job=_job_from_record(r),
        ml_gate_result=r["ml_gate_result"],
    )


def _evaluation_from_record(r: asyncpg.Record) -> JobEvaluation:
    """Build a joined JobEvaluation from a jobs+evaluations record.

    Args:
        r: Joined database record.

    Returns:
        Domain evaluation with optional Stage A and Stage B results.
    """
    return JobEvaluation(
        job=_job_from_record(r),
        stage_a=_stage_a_from_record(r),
        stage_b=_stage_b_from_record(r),
        stage_b_status=r.get("stage_b_status"),
        stage_b_blocks=_stage_b_blocks_from_record(r),
    )


def _stage_a_from_record(r: asyncpg.Record) -> StageAResult | None:
    """Build a Stage A result when the record has completed Stage A data.

    Args:
        r: Database record with evaluation columns.

    Returns:
        Stage A result for completed records; otherwise None.
    """
    if r.get("stage_a_status") != "completed":
        return None
    return StageAResult(
        score=r["stage_a_score"],
        one_line=r["stage_a_one_line"],
        timing_eligible=r["stage_a_timing_eligible"],
        model=r["stage_a_model"],
        prompt_hash=r["stage_a_prompt_hash"],
        resume_hash=r["stage_a_resume_hash"],
        cost_usd=r["stage_a_cost_usd"],
    )


def _as_json_obj(value: object) -> Any:
    """Decode a JSON/JSONB column value into a Python object.

    asyncpg returns ``json``/``jsonb`` columns as raw strings unless a custom
    type codec is registered, which this adapter does not do. Reads therefore
    decode the string here. Already-decoded values pass through unchanged so the
    helper stays correct if a codec is added later.

    Args:
        value: Raw column value from asyncpg (str when no codec is registered).

    Returns:
        The decoded JSON object, or the value unchanged when already decoded.
    """
    if isinstance(value, str):
        return cast(Any, json.loads(value))
    return value


def _stage_b_strengths(fit_json: dict[str, Any]) -> list[MatchItem]:
    """Parse the fit-analysis strong-match block into typed match items.

    Real evaluation rows can carry a missing or NULL ``strong_match`` key, so
    coalesce to an empty list rather than subscripting (which KeyErrors /
    TypeErrors on missing/null) — the detail/display path must never crash.
    """
    strong_match = fit_json.get("strong_match")
    if not isinstance(strong_match, list):
        return []
    return [
        MatchItem(
            requirement=m["requirement"],
            evidence=m.get("evidence_from_resume", m.get("evidence", "")),
        )
        for m in strong_match
    ]


def _stage_b_gaps(fit_json: dict[str, Any]) -> list[GapItem]:
    """Parse the fit-analysis gaps block into typed gap items.

    Real evaluation rows can carry a missing or NULL ``gaps`` key, so coalesce
    to an empty list rather than subscripting (which KeyErrors / TypeErrors on
    missing/null) — the detail/display path must never crash.
    """
    gaps = fit_json.get("gaps")
    if not isinstance(gaps, list):
        return []
    return [
        GapItem(
            requirement=g["requirement"],
            severity=_map_legacy_severity(g["severity"]),
            mitigation=g.get("mitigation") or "",
        )
        for g in gaps
    ]


def _stage_b_fit_analysis(fit_json: dict[str, Any]) -> FitAnalysis:
    """Build the normalized fit analysis (score + strengths + gaps)."""
    return FitAnalysis(
        score=fit_json["score_0_100"],
        strengths=_stage_b_strengths(fit_json),
        gaps=_stage_b_gaps(fit_json),
    )


def _stage_b_from_record(r: asyncpg.Record) -> StageBResult | None:
    """Build a Stage B result when the record has completed Stage B data.

    Args:
        r: Database record with evaluation columns.

    Returns:
        Stage B result for completed records with a verdict; otherwise None.
    """
    if r.get("stage_b_status") != "completed":
        return None
    if r.get("stage_b_verdict") is None:
        # Defensive: legacy/inconsistent rows can be 'completed' without a
        # verdict. Treat as no usable Stage B result rather than crashing.
        # The verdict-independent fit/blocks for the detail pane come from
        # _stage_b_blocks_from_record instead.
        return None

    fit_json = _as_json_obj(r["stage_b_fit_json"])
    if not isinstance(fit_json, dict) or fit_json.get("score_0_100") is None:
        # Defensive: real 'completed' rows can carry a verdict but a NULL/absent
        # fit score (102 such rows in the live corpus). The strict domain
        # FitAnalysis requires an int score, so we cannot build a StageBResult
        # here without crashing. Fall through to None; the verdict-independent
        # _stage_b_blocks_from_record feeds the detail display path instead.
        return None
    hooks_json = _as_json_obj(r["stage_b_hooks_json"])

    raw_blocks: dict[str, object] = {
        "verdict": _as_json_obj(r["stage_b_verdict_json"]),
        "jd_summary": _as_json_obj(r["stage_b_summary_json"]),
        "fit_analysis": fit_json,
        "resume_hooks": hooks_json,
    }

    return StageBResult(
        verdict=Verdict(r["stage_b_verdict"]),
        jd_summary=r["stage_b_jd_summary"],
        fit_analysis=_stage_b_fit_analysis(fit_json),
        resume_hooks=_read_legacy_hooks(hooks_json),
        model=r["stage_b_model"],
        prompt_hash=r["stage_b_prompt_hash"],
        resume_hash=r["stage_b_resume_hash"],
        cost_usd=r["stage_b_cost_usd"],
        raw_blocks=raw_blocks,
    )


def _stage_b_hooks_block(hooks_json: object) -> dict[str, object]:
    """Normalize the resume-hooks block to the detail wire shape.

    Prefers a structured ``lead_with``/``supporting``/``avoid_mentioning``
    object; otherwise derives lead/supporting from the flat hook list.
    """
    if isinstance(hooks_json, dict) and "lead_with" in hooks_json:
        supporting = hooks_json.get("supporting")
        avoid = hooks_json.get("avoid_mentioning")
        return {
            "lead_with": str(hooks_json.get("lead_with") or ""),
            "supporting": [str(s) for s in supporting]
            if isinstance(supporting, list)
            else [],
            "avoid_mentioning": [str(a) for a in avoid]
            if isinstance(avoid, list)
            else [],
        }
    flat = _read_legacy_hooks(hooks_json) if isinstance(hooks_json, dict) else []
    return {
        "lead_with": flat[0] if flat else "",
        "supporting": flat[1:],
        "avoid_mentioning": [],
    }


def _stage_b_jd_summary(r: asyncpg.Record) -> str:
    """Read the Stage B JD summary, flat column first then the JSON block.

    The flat ``stage_b_jd_summary`` is co-written with the verdict; a
    verdict-less legacy row may have nulled it while the JSON summary block
    survives, so fall back to ``stage_b_summary_json``'s ``role_in_3_lines``.

    Args:
        r: Database record with evaluation columns.

    Returns:
        The JD summary text, or an empty string when neither source has it.
    """
    flat = r.get("stage_b_jd_summary")
    if flat:
        return str(flat)
    summary_json = _as_json_obj(r.get("stage_b_summary_json"))
    if isinstance(summary_json, dict):
        return str(summary_json.get("role_in_3_lines") or "")
    return ""


def _stage_b_blocks_from_record(r: asyncpg.Record) -> dict[str, object] | None:
    """Build the verdict-independent Stage B display blocks for the detail.

    Returns the normalized display shape (jd_summary / fit_score / strengths
    / gaps / hooks) for any ``completed`` row carrying a usable fit JSON
    object — even one whose flat verdict is NULL, or whose ``score_0_100`` is
    absent/NULL — so the detail pane renders the same Stage B content the list
    row implies. The fit score is coalesced to None (rendered as "—") rather
    than fed to the strict domain ``FitAnalysis`` (which requires an int and
    would raise); strengths/gaps already coalesce missing blocks to []. The
    display DTO ``StageBDetail`` tolerates the null score and null summary.
    Returns None only when there is no completed fit JSON object at all.

    Args:
        r: Database record with evaluation columns.

    Returns:
        Normalized blocks dict, or None when no completed fit JSON exists.
    """
    if r.get("stage_b_status") != "completed":
        return None
    fit_json = _as_json_obj(r.get("stage_b_fit_json"))
    if not isinstance(fit_json, dict):
        return None
    score = fit_json.get("score_0_100")
    return {
        "jd_summary": _stage_b_jd_summary(r),
        "fit_score": score
        if isinstance(score, int) and not isinstance(score, bool)
        else None,
        "strengths": [
            {"requirement": s.requirement, "evidence": s.evidence}
            for s in _stage_b_strengths(fit_json)
        ],
        "gaps": [
            {
                "requirement": g.requirement,
                "severity": g.severity,
                "mitigation": g.mitigation,
            }
            for g in _stage_b_gaps(fit_json)
        ],
        "hooks": _stage_b_hooks_block(_as_json_obj(r.get("stage_b_hooks_json"))),
    }


_LEGACY_SEVERITY_MAP: dict[str, str] = {
    "blocker": "critical",
    "notable": "major",
    "minor": "minor",
}


def _map_legacy_severity(value: str) -> Severity:
    """Map legacy severity vocabulary to domain vocabulary.

    Args:
        value: Raw severity string (legacy or domain vocabulary).

    Returns:
        Typed Severity value.

    Raises:
        ValueError: If the severity value is not recognized.
    """
    mapped = _LEGACY_SEVERITY_MAP.get(value, value)
    if mapped not in VALID_SEVERITIES:
        raise ValueError(f"invalid gap severity: {value}")
    return cast(Severity, mapped)


def _read_legacy_hooks(hooks_json: dict[str, object]) -> list[str]:
    """Read resume hooks from legacy or Phase 0 JSON structure.

    Args:
        hooks_json: Parsed hooks JSON object.

    Returns:
        Flat list of hook strings.
    """
    if "lead_with" in hooks_json:
        lead = hooks_json["lead_with"]
        supporting = hooks_json.get("supporting", [])
        result = [str(lead)] if lead else []
        if isinstance(supporting, list):
            result.extend(str(s) for s in supporting)
        return result
    if "hooks" in hooks_json:
        raw_hooks = hooks_json["hooks"]
        if isinstance(raw_hooks, list):
            return [str(h) for h in raw_hooks]
    return []


def _validated_severity(value: str) -> Severity:
    """Validate and cast a severity string.

    Args:
        value: Raw severity string from the database.

    Returns:
        Typed Severity value.

    Raises:
        ValueError: If the severity value is not recognized.
    """
    if value not in VALID_SEVERITIES:
        raise ValueError(f"invalid gap severity: {value}")
    return cast(Severity, value)


def _pipeline_run_from_record(r: asyncpg.Record) -> PipelineRun:
    """Build a PipelineRun from an asyncpg Record.

    Args:
        r: Database record containing pipeline_runs columns.

    Returns:
        Domain pipeline run.
    """
    return PipelineRun(
        run_id=r["run_id"],
        started_at=r["started_at"],
        source=r["source"],
        status=r["status"],
        jobs_discovered=r["jobs_discovered"],
        jobs_inserted=r["jobs_inserted"],
        jobs_updated=r["jobs_updated"],
        jobs_filtered=r["jobs_filtered"],
        jobs_ml_gated=r["jobs_ml_gated"],
        stage_a_scored=r["stage_a_scored"],
        stage_b_scored=r["stage_b_scored"],
        jobs_scored=r["jobs_scored"],
        total_llm_cost_usd=r["total_llm_cost_usd"],
        errors=r["errors"],
        finished_at=r["finished_at"],
    )


# Insights aggregates (Phase 8). Totals and distributions are all-time;
# only the daily series is windowed. The verdict CASE mirrors the triage
# grouping: an explicit verdict wins; verdict-less threshold-skipped rows
# form the derived below_threshold bucket.
# ml_gate_passed counts gate survivors (ml_gate_result = 'pass') — the
# funnel-stage semantic — not gate failures; jobs never gated (NULL) count
# toward neither.
_INSIGHTS_TOTALS_SQL = """SELECT
    (SELECT COUNT(*) FROM jobs) AS total_jobs,
    (SELECT COUNT(*) FROM jobs WHERE ml_gate_result = 'pass')
        AS ml_gate_passed_jobs,
    (SELECT COUNT(*) FROM evaluations WHERE stage_a_at IS NOT NULL)
        AS evaluated_jobs,
    (SELECT COUNT(*) FROM applied) AS applied_jobs"""

_INSIGHTS_VERDICTS_SQL = """SELECT
        CASE WHEN stage_b_verdict IS NOT NULL THEN stage_b_verdict
             ELSE 'below_threshold' END AS bucket,
        COUNT(*) AS n
    FROM evaluations
    WHERE stage_b_verdict IS NOT NULL
       OR stage_b_status = 'skipped_below_threshold'
    GROUP BY bucket"""

_INSIGHTS_STATUSES_SQL = """SELECT status AS bucket, COUNT(*) AS n
    FROM job_status GROUP BY status"""


def _insights_day_sql(table: str, column: str) -> str:
    """Build the per-day count query for one insights measure.

    ``AT TIME ZONE 'UTC'`` pins the day buckets to UTC regardless of the
    session timezone; rows with a NULL timestamp never match the window.
    The window is closed on both ends (``[now - N days, now]``), so a
    future-dated timestamp never emits a future bucket.

    Args:
        table: Source table (in-repo literal, never user input).
        column: Timestamptz column to bucket (in-repo literal).

    Returns:
        SQL with one ``$1`` parameter: the window size in days.
    """
    return (
        f"SELECT ({column} AT TIME ZONE 'UTC')::date AS day, COUNT(*) AS n"
        f" FROM {table}"
        f" WHERE {column} >= now() - make_interval(days => $1)"
        f" AND {column} <= now()"
        " GROUP BY day"
    )


def _merge_insights_days(
    discovered: list[asyncpg.Record],
    evaluated: list[asyncpg.Record],
    applied: list[asyncpg.Record],
) -> list[InsightsDay]:
    """Merge the three per-day count queries into one ascending series.

    Only days appearing in at least one measure are emitted (consumers
    zero-fill gaps). Time complexity: O(n log n) — linear over the rows plus
    the final sort by day.

    Args:
        discovered: (day, n) rows bucketed on ``jobs.discovered_at``.
        evaluated: (day, n) rows bucketed on ``evaluations.stage_a_at``.
        applied: (day, n) rows bucketed on ``applied.applied_at``.

    Returns:
        Per-day funnel counts, ascending by day.
    """
    by_day: dict[date, dict[str, int]] = {}
    for measure, rows in (
        ("discovered", discovered),
        ("evaluated", evaluated),
        ("applied", applied),
    ):
        for r in rows:
            by_day.setdefault(r["day"], {})[measure] = int(r["n"])
    return [
        InsightsDay(
            day=day,
            discovered=counts.get("discovered", 0),
            evaluated=counts.get("evaluated", 0),
            applied=counts.get("applied", 0),
        )
        for day, counts in sorted(by_day.items())
    ]


def _status_info_from_record(r: asyncpg.Record) -> StatusInfo:
    """Build a StatusInfo from a joined job_status record.

    Args:
        r: Database record with status and job columns.

    Returns:
        Populated StatusInfo.
    """
    return StatusInfo(
        job_id=str(r["job_id"]),
        status=r["status"],
        next_followup_at=r["next_followup_at"],
        resume_variant=r["resume_variant"],
        notes=r["notes"],
        last_status_change_at=r["last_status_change_at"]
        or datetime(1970, 1, 1, tzinfo=UTC),
        company=r["company"],
        title=r["title"],
    )


def _attention_item_from_record(
    r: asyncpg.Record, reason: str
) -> WorkflowAttentionItem:
    """Build a WorkflowAttentionItem from a joined query record.

    Args:
        r: Database record with job and status columns.
        reason: Bucket reason label.

    Returns:
        Populated attention item.
    """
    return WorkflowAttentionItem(
        job_id=str(r["job_id"]),
        title=r["title"],
        company=r["company"],
        url=r["url"],
        status=r["status"],
        last_status_change_at=r["last_status_change_at"]
        or datetime(1970, 1, 1, tzinfo=UTC),
        next_followup_at=r["next_followup_at"],
        notes=r["notes"],
        reason=reason,
        days_since=r["days_since"],
    )


def _company_from_record(r: asyncpg.Record) -> CompanyRecord:
    """Build a CompanyRecord from an asyncpg Record.

    Args:
        r: Database record containing companies table columns.

    Returns:
        Domain company record.
    """
    return CompanyRecord(
        slug=r["slug"],
        ats_vendor=r["ats_vendor"],
        ats_override=bool(r["ats_override"]),
        last_verified_at=r["last_verified_at"],
        last_probe_attempt_at=r["last_probe_attempt_at"],
        job_count_last_scan=r["job_count_last_scan"],
        consecutive_discover_failures=r["consecutive_discover_failures"],
        notes=r["notes"],
    )


# ---------------------------------------------------------------------------
# Private helpers for application stats
# ---------------------------------------------------------------------------

_INTERVIEW_STATUSES = {"interviewing", "offer"}


def _median(values: list[int]) -> float | None:
    """Compute the median of an integer list.

    Args:
        values: Integer list (will be sorted in-place).

    Returns:
        Median as float, or None if the list is empty.
    """
    if not values:
        return None
    values.sort()
    n = len(values)
    if n % 2 == 1:
        return float(values[n // 2])
    return (values[n // 2 - 1] + values[n // 2]) / 2.0


def _count_outcomes(
    job_response_statuses: dict[int, set[str]],
) -> tuple[int, int, int, int]:
    """Count response, interview, offer, and rejection totals.

    Args:
        job_response_statuses: Mapping of job_id to sets of reached
            response statuses.

    Returns:
        Tuple of (response_count, interview_count, offer_count,
        rejection_count).
    """
    response_count = len(job_response_statuses)
    interview_count = sum(
        1 for s in job_response_statuses.values() if s & _INTERVIEW_STATUSES
    )
    offer_count = sum(1 for s in job_response_statuses.values() if "offer" in s)
    rejection_count = sum(1 for s in job_response_statuses.values() if "rejected" in s)
    return response_count, interview_count, offer_count, rejection_count


# ---------------------------------------------------------------------------
# JSONB serialization helpers
# ---------------------------------------------------------------------------


def _json_or_none(value: Any) -> str | None:
    """Serialize a value to JSON string, or None if already None.

    Args:
        value: Value to serialize (list or dict).

    Returns:
        JSON string or None.
    """
    if value is None:
        return None
    return json.dumps(value, sort_keys=True)


def _stage_b_block_json(result: StageBResult, key: str) -> str:
    """Serialize one Stage B block for JSONB column storage.

    Args:
        result: Stage B result to persist.
        key: Block key to serialize.

    Returns:
        JSON string for the block.
    """
    blocks = result.raw_blocks or {}
    if key in blocks:
        return json.dumps(blocks[key], sort_keys=True)
    return json.dumps(_derived_stage_b_block(result, key), sort_keys=True)


def _derived_stage_b_block(result: StageBResult, key: str) -> dict[str, object]:
    if key == "verdict":
        return {"recommendation": result.verdict.value}
    if key == "jd_summary":
        return _derived_jd_summary_block(result)
    if key == "fit_analysis":
        return _derived_fit_analysis_block(result)
    if key == "resume_hooks":
        return _derived_resume_hooks_block(result)
    raise ValueError(f"unknown Stage B block: {key}")


def _derived_jd_summary_block(result: StageBResult) -> dict[str, object]:
    return {
        "role_in_3_lines": result.jd_summary,
        "must_haves": [],
        "nice_to_haves": [],
        "red_flags_in_jd": [],
    }


def _derived_fit_analysis_block(result: StageBResult) -> dict[str, object]:
    return {
        "score_0_100": result.fit_analysis.score,
        "strong_match": [
            {
                "requirement": item.requirement,
                "evidence_from_resume": item.evidence,
            }
            for item in result.fit_analysis.strengths
        ],
        "gaps": [
            {
                "requirement": item.requirement,
                "severity": item.severity,
                "mitigation": item.mitigation,
            }
            for item in result.fit_analysis.gaps
        ],
    }


def _derived_resume_hooks_block(result: StageBResult) -> dict[str, object]:
    hooks = result.resume_hooks
    return {
        "lead_with": hooks[0] if hooks else "",
        "supporting": hooks[1:],
        "avoid_mentioning": [],
    }


# Marker mark_stale_closed stamps on heuristically-closed no-JD rows. Shared
# with get_closed_canonical_ids, which excludes these rows; both sites must use
# this constant so the stamp and the exclusion cannot drift apart.
_STALE_BACKFILL_MARKER = "backfill:stale-no-jd"


# ---------------------------------------------------------------------------
# Pending-queue query builders
# ---------------------------------------------------------------------------


# Error rows over MAX_STAGE_RETRIES drop out of the pending queues (plan Task 1);
# they stay terminally in 'error' for manual triage instead of retrying forever.
_PG_STAGE_A_UNDER_CAP = (
    "(evaluations.stage_a_status IS DISTINCT FROM 'error' "
    f"OR evaluations.stage_a_error_count < {MAX_STAGE_RETRIES})"
)
_PG_STAGE_B_UNDER_CAP = (
    "(evaluations.stage_b_status IS DISTINCT FROM 'error' "
    f"OR evaluations.stage_b_error_count < {MAX_STAGE_RETRIES})"
)
_PG_EVALUATION_CLAIM_TTL = timedelta(hours=1)

# The jd_quality -> rank CASE used to decide whether an incoming JD outranks the
# stored one (mirrors domain.quality.quality_rank). Reused by save_job's upsert.
_PG_STORED_JD_RANK = (
    "(CASE jobs.jd_quality "
    "WHEN 'full' THEN 5 WHEN 'good' THEN 4 "
    "WHEN 'partial' THEN 3 WHEN 'stub' THEN 2 "
    "WHEN 'missing' THEN 1 WHEN 'abandoned' THEN 0 "
    "ELSE -1 END)"
)
# The JD that actually WINS save_job's upsert: the incoming JD only when it
# outranks (>=) the stored one, else the preserved stored JD. ``$18`` is the
# incoming quality_rank bind. Identical to the jd_text assignment's CASE.
_PG_WINNING_JD_TEXT = (
    f"(CASE WHEN $18::int >= {_PG_STORED_JD_RANK} "
    "THEN COALESCE(EXCLUDED.jd_text, jobs.jd_text) ELSE jobs.jd_text END)"
)
# A gate INPUT feature changed iff the title or the WINNING JD differs from the
# stored row. Drives save_job's stale-ml_gate reset.
_PG_GATE_INPUT_CHANGED = (
    "(EXCLUDED.title IS DISTINCT FROM jobs.title "
    f"OR {_PG_WINNING_JD_TEXT} IS DISTINCT FROM jobs.jd_text)"
)
# Reset (NULL) the five ml_gate_* verdict columns on a gate-input change, else
# preserve them. Appended to save_job's ON CONFLICT DO UPDATE SET list.
_PG_RESET_ML_GATE_SET = ",\n".join(
    f"ml_gate_{col} = CASE WHEN {_PG_GATE_INPUT_CHANGED} "
    f"THEN NULL ELSE jobs.ml_gate_{col} END"
    for col in ("score", "result", "fail_reason", "at", "version")
)


def _pg_corpus_condition(corpus: str) -> str:
    """Return the WHERE fragment selecting the requested Stage A corpus.

    Args:
        corpus: "unrated" (no eval row or stage A error), "all" (no
            restriction), or "failed" (Stage A errors only).

    Returns:
        SQL boolean fragment; empty string for "all".

    Raises:
        ValueError: If corpus is not a recognized value.
    """
    if corpus == "unrated":
        return (
            "(evaluations.job_id IS NULL "
            "OR evaluations.stage_a_status IS NULL "
            "OR evaluations.stage_a_status = 'error')"
        )
    if corpus == "failed":
        return "evaluations.stage_a_status = 'error'"
    if corpus == "all":
        return ""
    raise ValueError(f"unknown corpus: {corpus!r}")


def _stage_a_pending_filters(
    *,
    quality_bands: frozenset[str] | None,
    corpus: str,
    max_days: int | None,
    now: datetime,
) -> tuple[list[str], list[Any]]:
    """Build shared Stage A pending filters.

    Always excludes ``closed_at IS NOT NULL`` rows (job-liveness): a posting
    confirmed gone by a JD fetch never enters the funnel via any consumer
    (``load_gate_candidates`` and every Stage-A claim path).

    Args:
        quality_bands: Optional jd_quality allow-list.
        corpus: "unrated", "all", or "failed".
        max_days: Optional freshness window on discovered_at.
        now: Reference time for the freshness cutoff.

    Returns:
        WHERE conditions and positional (asyncpg) parameters.
    """
    conditions: list[str] = []
    params: list[Any] = []
    corpus_clause = _pg_corpus_condition(corpus)
    if corpus_clause:
        conditions.append(corpus_clause)
    conditions.append(_PG_STAGE_A_UNDER_CAP)
    # Job-liveness (closed_at feature): a req confirmed gone by JD fetch
    # (404/410/403) is stamped closed_at and must never enter the funnel. This
    # predicate is shared, so it covers load_gate_candidates AND every claim
    # path (claim_pending_stage_a, claim_stage_a_by_ids).
    conditions.append("jobs.closed_at IS NULL")
    if quality_bands:
        params.append(sorted(quality_bands))
        conditions.append(f"jobs.jd_quality = ANY(${len(params)})")
    if max_days is not None:
        params.append(now - timedelta(days=max_days))
        conditions.append(f"jobs.discovered_at >= ${len(params)}")
    return conditions, params


def _build_pending_stage_a_query(
    *,
    limit: int,
    quality_bands: frozenset[str] | None,
    corpus: str,
    max_days: int | None,
    now: datetime,
) -> tuple[str, list[Any]]:
    """Build the Stage A pending query with optional legacy parity filters.

    Args:
        limit: Max rows.
        quality_bands: Optional jd_quality allow-list.
        corpus: "unrated", "all", or "failed".
        max_days: Optional freshness window on discovered_at.
        now: Reference time for the freshness cutoff.

    Returns:
        SQL string and its positional (asyncpg) parameters.
    """
    conditions, params = _stage_a_pending_filters(
        quality_bands=quality_bands,
        corpus=corpus,
        max_days=max_days,
        now=now,
    )
    where = " AND ".join(conditions) if conditions else "TRUE"
    params.append(limit)
    sql = (
        "SELECT jobs.* FROM jobs "
        "LEFT JOIN evaluations ON evaluations.job_id = jobs.id "
        f"WHERE {where} "
        "ORDER BY jobs.discovered_at DESC, jobs.id DESC "
        f"LIMIT ${len(params)}"
    )
    return sql, params


def _stage_b_pending_filters(
    *,
    max_days: int | None,
    stage_a_threshold: int | None,
    now: datetime,
) -> tuple[list[str], list[Any]]:
    """Build shared Stage B pending filters.

    Args:
        max_days: Optional freshness window on discovered_at.
        stage_a_threshold: Optional minimum Stage A score.
        now: Reference time for the freshness cutoff.

    Returns:
        WHERE conditions and positional (asyncpg) parameters.
    """
    conditions = [
        "evaluations.stage_a_status = 'completed'",
        "(evaluations.stage_b_status IS NULL OR evaluations.stage_b_status = 'error')",
        _PG_STAGE_B_UNDER_CAP,
    ]
    params: list[Any] = []
    if max_days is not None:
        params.append(now - timedelta(days=max_days))
        conditions.append(f"jobs.discovered_at >= ${len(params)}")
    if stage_a_threshold is not None:
        params.append(stage_a_threshold)
        conditions.append(f"evaluations.stage_a_score >= ${len(params)}")
    return conditions, params


def _build_pending_stage_b_query(
    *,
    limit: int,
    max_days: int | None,
    stage_a_threshold: int | None,
    now: datetime,
) -> tuple[str, list[Any]]:
    """Build the Stage B pending query (Stage A completed, Stage B pending).

    Args:
        limit: Max rows.
        max_days: Optional freshness window on discovered_at.
        stage_a_threshold: Optional minimum Stage A score.
        now: Reference time for the freshness cutoff.

    Returns:
        SQL string and its positional (asyncpg) parameters.
    """
    conditions, params = _stage_b_pending_filters(
        max_days=max_days,
        stage_a_threshold=stage_a_threshold,
        now=now,
    )
    params.append(limit)
    where = " AND ".join(conditions)
    sql = (
        "SELECT jobs.* FROM jobs "
        "JOIN evaluations ON evaluations.job_id = jobs.id "
        f"WHERE {where} "
        "ORDER BY jobs.discovered_at DESC, jobs.id DESC "
        f"LIMIT ${len(params)}"
    )
    return sql, params


def _stage_a_claim_status_condition(corpus: str, stale_ref: str) -> str:
    if corpus == "all":
        return (
            "(evaluations.stage_a_status IS DISTINCT FROM 'in_progress' "
            f"OR evaluations.updated_at < {stale_ref})"
        )
    if corpus == "failed":
        return (
            "(evaluations.stage_a_status = 'error' OR "
            "(evaluations.stage_a_status = 'in_progress' "
            f"AND evaluations.updated_at < {stale_ref} "
            "AND evaluations.stage_a_error IS NOT NULL))"
        )
    if corpus == "unrated":
        return (
            "(evaluations.job_id IS NULL OR evaluations.stage_a_status IS NULL "
            "OR evaluations.stage_a_status = 'error' OR "
            "(evaluations.stage_a_status = 'in_progress' "
            f"AND evaluations.updated_at < {stale_ref} "
            "AND (evaluations.stage_a_score IS NULL "
            "OR evaluations.stage_a_error IS NOT NULL)))"
        )
    raise ValueError(f"unknown corpus: {corpus!r}")


def _stage_a_claim_update_condition(corpus: str, stale_ref: str) -> str:
    if corpus in {"all", "failed"}:
        return _stage_a_claim_status_condition(corpus, stale_ref)
    if corpus == "unrated":
        return (
            "(evaluations.stage_a_status IS NULL OR evaluations.stage_a_status = 'error' "
            "OR (evaluations.stage_a_status = 'in_progress' "
            f"AND evaluations.updated_at < {stale_ref} "
            "AND (evaluations.stage_a_score IS NULL "
            "OR evaluations.stage_a_error IS NOT NULL)))"
        )
    raise ValueError(f"unknown corpus: {corpus!r}")


def _claim_stage_a_sql(*, where: str, update_condition: str, limit_ref: str) -> str:
    """Assemble the Stage A atomic-claim CTE SQL.

    Shared by the queue-wide claim and the id-restricted claim so the
    ``FOR UPDATE ... SKIP LOCKED`` + ``in_progress`` upsert lives in one place.

    Args:
        where: Candidate-selection WHERE clause.
        update_condition: Guard for the ON CONFLICT upsert (stale/retry rules).
        limit_ref: Positional placeholder for the candidate LIMIT.

    Returns:
        The claim CTE SQL string.
    """
    return f"""WITH candidates AS (
                SELECT jobs.id FROM jobs
                LEFT JOIN evaluations ON evaluations.job_id = jobs.id
                WHERE {where}
                ORDER BY jobs.discovered_at DESC, jobs.id DESC
                LIMIT {limit_ref}
                FOR UPDATE OF jobs SKIP LOCKED
            ),
            claimed AS (
                INSERT INTO evaluations (job_id, stage_a_status, updated_at)
                SELECT id, 'in_progress', now() FROM candidates
                ON CONFLICT (job_id) DO UPDATE SET
                    stage_a_status = 'in_progress',
                    updated_at = now()
                WHERE {update_condition}
                RETURNING job_id
            )
            SELECT jobs.* FROM jobs
            JOIN claimed ON claimed.job_id = jobs.id
            ORDER BY jobs.discovered_at DESC, jobs.id DESC"""


def _build_claim_stage_a_query(
    *,
    limit: int,
    quality_bands: frozenset[str] | None,
    corpus: str,
    max_days: int | None,
    now: datetime,
) -> tuple[str, list[Any]]:
    """Build the Stage A atomic claim query."""
    conditions, params = _stage_a_pending_filters(
        quality_bands=quality_bands,
        corpus="all",
        max_days=max_days,
        now=now,
    )
    params.append(now - _PG_EVALUATION_CLAIM_TTL)
    stale_ref = f"${len(params)}"
    conditions.append(_stage_a_claim_status_condition(corpus, stale_ref))
    where = " AND ".join(conditions) if conditions else "TRUE"
    params.append(limit)
    update_condition = _stage_a_claim_update_condition(corpus, stale_ref)
    return (
        _claim_stage_a_sql(
            where=where,
            update_condition=update_condition,
            limit_ref=f"${len(params)}",
        ),
        params,
    )


def _numeric_job_ids(job_ids: list[str]) -> list[int]:
    """Coerce store ids to ints, skipping non-numeric ones.

    Store ids are bigint-as-str, so a non-numeric id matches no row; skipping
    them keeps ``claim_stage_a_by_ids`` crash-free (no ``ValueError``) instead of
    failing the whole claim. An all-non-numeric input yields ``[]``, which the
    caller treats as a no-op (no query, no writes).
    """
    numeric: list[int] = []
    for jid in job_ids:
        try:
            numeric.append(int(jid))
        except (ValueError, TypeError):
            continue
    return numeric


def _build_claim_stage_a_by_ids_query(
    *,
    job_ids: list[int],
    limit: int,
    quality_bands: frozenset[str] | None,
    corpus: str,
    max_days: int | None,
    now: datetime,
) -> tuple[str, list[Any]]:
    """Build the Stage A atomic claim query restricted to an explicit id set.

    Identical claim machinery to ``_build_claim_stage_a_query`` (stale-takeover /
    retry-cap / ``SKIP LOCKED`` upsert), but the candidate scan is additionally
    constrained to ``job_ids``. Callers must short-circuit empty ``job_ids``
    (already coerced to bigint via ``_numeric_job_ids``).

    Args:
        job_ids: Numeric store ids to restrict the claim to (non-empty bigints).
        limit: Max rows.
        quality_bands: Optional jd_quality allow-list.
        corpus: "unrated", "all", or "failed".
        max_days: Optional freshness window on discovered_at.
        now: Reference time for freshness and the stale-claim cutoff.

    Returns:
        SQL string and its positional (asyncpg) parameters.
    """
    conditions, params = _stage_a_pending_filters(
        quality_bands=quality_bands,
        corpus="all",
        max_days=max_days,
        now=now,
    )
    params.append(job_ids)
    conditions.append(f"jobs.id = ANY(${len(params)})")
    params.append(now - _PG_EVALUATION_CLAIM_TTL)
    stale_ref = f"${len(params)}"
    conditions.append(_stage_a_claim_status_condition(corpus, stale_ref))
    where = " AND ".join(conditions)
    params.append(limit)
    update_condition = _stage_a_claim_update_condition(corpus, stale_ref)
    return (
        _claim_stage_a_sql(
            where=where,
            update_condition=update_condition,
            limit_ref=f"${len(params)}",
        ),
        params,
    )


def _append_unrated_completed_suppression(conditions: list[str]) -> None:
    """Append the unrated-only completed-exclusion + twin-suppression predicates.

    Scoped to the incremental ``corpus='unrated'`` flow (the caller gates on
    corpus). Two predicates, neither parameterized:

    1. Completed-exclusion — a row already Stage-A ``completed`` (or whose
       cluster was) is done, so drop it from the default incremental load.
    2. Twin-cluster cross-run suppression — once ANY member of a row's twin
       cluster (shared ``(company_norm, title_norm)``) is Stage-A ``completed``
       the cluster has been scored, so drop every still-pending sibling; else the
       survivors re-elect a new representative next run and incur duplicate LLM
       cost, violating dedupe's "score each cluster once". Only fires when BOTH
       norms are non-blank, mirroring dedupe's blank-norm singleton rule (a blank
       company_norm OR title_norm forms its own cluster and must never suppress
       another blank-norm row).

    Args:
        conditions: WHERE-clause fragment list, appended in place.
    """
    conditions.append(
        "(evaluations.job_id IS NULL "
        "OR evaluations.stage_a_status IS DISTINCT FROM 'completed')"
    )
    conditions.append(
        "(NOT (COALESCE(jobs.company_norm,'') <> '' "
        "AND COALESCE(jobs.title_norm,'') <> '') "
        "OR NOT EXISTS (SELECT 1 FROM jobs twin "
        "JOIN evaluations twin_eval ON twin_eval.job_id = twin.id "
        "WHERE twin.company_norm = jobs.company_norm "
        "AND twin.title_norm = jobs.title_norm "
        "AND twin_eval.stage_a_status = 'completed'))"
    )


def _build_gate_candidates_query(
    *,
    limit: int,
    quality_bands: frozenset[str] | None,
    corpus: str,
    max_days: int | None,
    exclude_gate_failed: bool,
    now: datetime,
    after: tuple[datetime, int] | None = None,
) -> tuple[str, list[Any]]:
    """Build the NON-claiming ML-gate candidate query.

    Reuses the shared Stage A eligibility filters (quality_bands / freshness on
    ``discovered_at`` / retry-cap) and the SAME stale-takeover corpus predicate
    the claim builders use (``_stage_a_claim_status_condition``). For the
    incremental DEFAULT corpus (``'unrated'``) it ALSO adds the
    not-yet-Stage-A-scored exclusion (status IS DISTINCT FROM 'completed') plus a
    twin-cluster cross-run suppression; both are scoped to ``'unrated'`` ONLY (see
    ``_append_unrated_completed_suppression``). When ``exclude_gate_failed`` is
    set, ``jobs.ml_gate_result IS DISTINCT FROM 'fail'`` is appended for every
    corpus.

    Corpus-scoped completed handling — because the funnel is the sole path to
    survivor ids, the completed-exclusion / twin-suppression must AGREE with each
    corpus's claim semantics or rows get filtered before
    ``claim_stage_a_by_ids`` can claim them:
      * ``'unrated'`` — completed rows + completed-cluster twins are dropped
        (incremental "score new work once").
      * ``'all'`` — explicit full re-evaluation: completed rows AND completed-
        cluster twins are RE-ADMITTED, mirroring the claim's stale-takeover that
        permits non-locked completed rows.
      * ``'failed'`` — targets ``'error'`` rows (the claim status predicate
        already restricts the set); a failed twin whose cluster sibling completed
        stays re-scorable, so suppression must not fire.

    Sharing the claim status predicate (instead of the narrower
    ``_pg_corpus_condition``) means a row stuck ``in_progress`` past the claim
    TTL by an interrupted *scoring* run re-enters the funnel under "unrated",
    becomes a survivor, and is re-claimable by ``claim_stage_a_by_ids`` (whose
    own stale-takeover then succeeds). FRESH ``in_progress`` rows (actively owned
    by another run) are excluded. Rows with a NULL or 'pass' gate that are not
    yet Stage-A-scored are included (crash recovery). This reads only — no claim,
    no in_progress write.

    ``after`` is an optional keyset cursor ``(discovered_at, id)``: when set, only
    rows strictly older in the ``ORDER BY jobs.discovered_at DESC, jobs.id DESC``
    sense are returned (``(discovered_at, id) < (ts, id)``). The funnel uses it to
    page PAST hard-filtered drops so an eligible older row behind a blocked newest
    prefix is not starved; every other predicate above still applies per page.

    Args:
        limit: Max rows (one page).
        quality_bands: Optional jd_quality allow-list.
        corpus: "unrated", "all", or "failed".
        max_days: Optional freshness window on discovered_at.
        exclude_gate_failed: When True, drop rows whose ml_gate_result is 'fail'.
        now: Reference time for the freshness + stale-claim cutoffs.
        after: Optional ``(discovered_at, id)`` keyset cursor for the next page.

    Returns:
        SQL string and its positional (asyncpg) parameters.
    """
    conditions, params = _stage_a_pending_filters(
        quality_bands=quality_bands,
        corpus="all",
        max_days=max_days,
        now=now,
    )
    params.append(now - _PG_EVALUATION_CLAIM_TTL)
    stale_ref = f"${len(params)}"
    conditions.append(_stage_a_claim_status_condition(corpus, stale_ref))
    if after is not None:
        cursor_ts, cursor_id = after
        params.append(cursor_ts)
        params.append(cursor_id)
        # Keyset "next page" predicate, consistent with the row tuple ordering
        # ORDER BY jobs.discovered_at DESC, jobs.id DESC.
        conditions.append(
            f"(jobs.discovered_at, jobs.id) < (${len(params) - 1}, ${len(params)})"
        )
    # Completed-exclusion + twin-cluster suppression are scoped to the
    # incremental DEFAULT flow (corpus='unrated') ONLY. The funnel is now the
    # sole path to survivor ids, so applying them to every corpus would silently
    # filter completed rows BEFORE claim_stage_a_by_ids could re-claim them,
    # breaking the explicit full re-evaluation (corpus='all') and the failed
    # retry (corpus='failed'). Both of those corpora must mirror their
    # _stage_a_claim_status_condition semantics: 'all' permits non-locked
    # completed rows; 'failed' targets 'error' rows (and a failed twin whose
    # cluster sibling completed must stay re-scorable). So neither predicate
    # fires outside 'unrated'.
    if corpus == "unrated":
        _append_unrated_completed_suppression(conditions)
    if exclude_gate_failed:
        conditions.append("jobs.ml_gate_result IS DISTINCT FROM 'fail'")
    where = " AND ".join(conditions) if conditions else "TRUE"
    params.append(limit)
    sql = (
        "SELECT jobs.* FROM jobs "
        "LEFT JOIN evaluations ON evaluations.job_id = jobs.id "
        f"WHERE {where} "
        "ORDER BY jobs.discovered_at DESC, jobs.id DESC "
        f"LIMIT ${len(params)}"
    )
    return sql, params


def _build_claimable_stage_a_query(
    *,
    limit: int,
    quality_bands: frozenset[str] | None,
    corpus: str,
    max_days: int | None,
    now: datetime,
) -> tuple[str, list[Any]]:
    """Build the read-only Stage A claim preview query."""
    conditions, params = _stage_a_pending_filters(
        quality_bands=quality_bands,
        corpus="all",
        max_days=max_days,
        now=now,
    )
    params.append(now - _PG_EVALUATION_CLAIM_TTL)
    stale_ref = f"${len(params)}"
    conditions.append(_stage_a_claim_status_condition(corpus, stale_ref))
    params.append(limit)
    where = " AND ".join(conditions) if conditions else "TRUE"
    return (
        "SELECT jobs.* FROM jobs "
        "LEFT JOIN evaluations ON evaluations.job_id = jobs.id "
        f"WHERE {where} "
        "ORDER BY jobs.discovered_at DESC, jobs.id DESC "
        f"LIMIT ${len(params)}",
        params,
    )


def _build_claim_stage_b_query(
    *,
    limit: int,
    max_days: int | None,
    stage_a_threshold: int | None,
    now: datetime,
) -> tuple[str, list[Any]]:
    """Build the Stage B atomic claim query."""
    conditions = [
        "evaluations.stage_a_status = 'completed'",
        _PG_STAGE_B_UNDER_CAP,
    ]
    params: list[Any] = []
    if max_days is not None:
        params.append(now - timedelta(days=max_days))
        conditions.append(f"jobs.discovered_at >= ${len(params)}")
    if stage_a_threshold is not None:
        params.append(stage_a_threshold)
        conditions.append(f"evaluations.stage_a_score >= ${len(params)}")
    params.append(now - _PG_EVALUATION_CLAIM_TTL)
    stale_ref = f"${len(params)}"
    stage_b_claim_condition = (
        "(evaluations.stage_b_status IS NULL OR evaluations.stage_b_status = 'error' "
        "OR (evaluations.stage_b_status = 'in_progress' "
        f"AND evaluations.updated_at < {stale_ref} "
        "AND evaluations.stage_b_verdict IS NULL))"
    )
    conditions.append(stage_b_claim_condition)
    params.append(limit)
    where = " AND ".join(conditions)
    return (
        f"""WITH candidates AS (
                SELECT jobs.id FROM jobs
                JOIN evaluations ON evaluations.job_id = jobs.id
                WHERE {where}
                ORDER BY jobs.discovered_at DESC, jobs.id DESC
                LIMIT ${len(params)}
                FOR UPDATE OF evaluations SKIP LOCKED
            ),
            claimed AS (
                UPDATE evaluations
                SET stage_b_status = 'in_progress', updated_at = now()
                FROM candidates
                WHERE evaluations.job_id = candidates.id
                  AND (
                    evaluations.stage_b_status IS NULL
                    OR evaluations.stage_b_status = 'error'
                    OR (
                        evaluations.stage_b_status = 'in_progress'
                        AND evaluations.updated_at < {stale_ref}
                        AND evaluations.stage_b_verdict IS NULL
                    )
                  )
                RETURNING evaluations.job_id
            )
            SELECT jobs.* FROM jobs
            JOIN claimed ON claimed.job_id = jobs.id
            ORDER BY jobs.discovered_at DESC, jobs.id DESC""",
        params,
    )


def _build_stage_b_preview_query(
    *,
    limit: int,
    max_days: int | None,
    stage_a_threshold: int,
    now: datetime,
) -> tuple[str, list[Any]]:
    """Build the read-only Stage B queue preview query."""
    conditions = [
        "evaluations.stage_a_status = 'completed'",
        "evaluations.stage_a_score >= $1",
        _PG_STAGE_B_UNDER_CAP,
    ]
    params: list[Any] = [stage_a_threshold]
    if max_days is not None:
        params.append(now - timedelta(days=max_days))
        conditions.append(f"jobs.discovered_at >= ${len(params)}")
    params.append(now - _PG_EVALUATION_CLAIM_TTL)
    stale_ref = f"${len(params)}"
    conditions.append(
        "("
        "evaluations.stage_b_status IS NULL OR "
        "evaluations.stage_b_status = 'error' OR "
        "evaluations.stage_b_status = 'skipped_below_threshold' OR "
        "(evaluations.stage_b_status = 'in_progress' AND "
        f"evaluations.updated_at < {stale_ref} AND "
        "evaluations.stage_b_verdict IS NULL)"
        ")"
    )
    params.append(limit)
    sql = (
        "SELECT jobs.* FROM jobs "
        "JOIN evaluations ON evaluations.job_id = jobs.id "
        f"WHERE {' AND '.join(conditions)} "
        "ORDER BY jobs.discovered_at DESC, jobs.id DESC "
        f"LIMIT ${len(params)}"
    )
    return sql, params


def _row_to_interview_round(row: asyncpg.Record) -> InterviewRound:
    """Build an InterviewRound from an asyncpg Record.

    Args:
        row: Database record containing interview_rounds columns.

    Returns:
        Domain interview round.
    """
    return InterviewRound(
        id=row["id"],
        job_id=row["job_id"],
        round_index=row["round_index"],
        label=row["label"],
        scheduled_at=row["scheduled_at"],
        completed_at=row["completed_at"],
        notes=row["notes"],
        created_at=row["created_at"],
    )


# ---------------------------------------------------------------------------
# PostgresStore
# ---------------------------------------------------------------------------


class PostgresStore:
    """PostgreSQL-backed JobStore implementation using asyncpg connection pool.

    All methods use asyncpg's native ``$1, $2, ...`` parameter placeholders
    and JSONB columns. Transactions use ``async with conn.transaction():``.
    """

    def __init__(self, dsn: str, **pool_kwargs: Any) -> None:
        """Create a store for a PostgreSQL connection string.

        Args:
            dsn: PostgreSQL connection string (e.g.
                ``postgresql://user:pass@host/db``).
            **pool_kwargs: Additional keyword arguments forwarded to
                ``asyncpg.create_pool`` (e.g. ``min_size``, ``max_size``).
        """
        self._dsn = dsn
        self._pool_kwargs = pool_kwargs
        self._pool: asyncpg.Pool | None = None
        # Dedicated connection + transaction held across a bulk-import run so all
        # bulk_insert_* calls share one transaction (the pool would otherwise
        # hand each call a different connection).
        self._import_conn: asyncpg.Connection | None = None
        self._import_tx: Any = None

    async def connect(self) -> None:
        """Open the asyncpg connection pool.

        Pins the session timezone to UTC so CURRENT_DATE / ::date truncation use
        UTC day boundaries. Without this, date-bucketing queries (digest_stats,
        follow-up due, cost range) would diverge on a non-UTC Postgres server.

        Raises:
            Exception: If pool creation fails.
        """
        if self._pool is not None:
            return
        pool_kwargs = dict(self._pool_kwargs)
        server_settings = {"timezone": "UTC", **pool_kwargs.pop("server_settings", {})}
        self._pool = await asyncpg.create_pool(
            self._dsn, server_settings=server_settings, **pool_kwargs
        )

    async def close(self) -> None:
        """Close the asyncpg connection pool when open."""
        if self._pool is not None:
            pool = self._pool
            self._pool = None
            await pool.close()

    def _get_pool(self) -> asyncpg.Pool:
        """Return the live pool or raise if not connected.

        Returns:
            The asyncpg connection pool.

        Raises:
            RuntimeError: If the store has not been connected.
        """
        if self._pool is None:
            raise RuntimeError("PostgresStore is not connected")
        return self._pool

    # ------------------------------------------------------------------
    # BulkImportPort + ParityReadPort (legacy migration)
    # ------------------------------------------------------------------

    _PARITY_TABLES = frozenset(
        {
            "jobs",
            "evaluations",
            "job_status",
            "job_status_history",
            "applied",
            "resume_snapshots",
            "resume_variants",
            "companies",
            "cost_ledger",
            "state",
            "pipeline_runs",
        }
    )

    def _import_connection(self) -> asyncpg.Connection:
        """Return the connection bound to the active import transaction.

        Returns:
            The held asyncpg connection.

        Raises:
            RuntimeError: If called outside an import transaction.
        """
        if self._import_conn is None:
            raise RuntimeError("bulk import called outside an import transaction")
        return self._import_conn

    async def begin_import_transaction(self) -> None:
        """Acquire a dedicated connection and open an import transaction."""
        conn = await self._get_pool().acquire()
        tx = conn.transaction()
        await tx.start()
        self._import_conn = conn
        self._import_tx = tx

    async def commit_import_transaction(self) -> None:
        """Commit the import transaction and release its connection."""
        if self._import_tx is None or self._import_conn is None:
            return
        await self._import_tx.commit()
        await self._get_pool().release(self._import_conn)
        self._import_tx = None
        self._import_conn = None

    async def rollback_import_transaction(self) -> None:
        """Roll back the import transaction and release its connection."""
        if self._import_tx is None or self._import_conn is None:
            return
        await self._import_tx.rollback()
        await self._get_pool().release(self._import_conn)
        self._import_tx = None
        self._import_conn = None

    async def disable_triggers(self) -> None:
        """Disable the auto-seed trigger on jobs during import."""
        async with self._get_pool().acquire() as conn:
            await conn.execute("ALTER TABLE jobs DISABLE TRIGGER USER")

    async def enable_triggers(self) -> None:
        """Re-enable the auto-seed trigger on jobs after import."""
        async with self._get_pool().acquire() as conn:
            await conn.execute("ALTER TABLE jobs ENABLE TRIGGER USER")

    async def reset_sequences(self) -> None:
        """Advance SERIAL sequences past the explicitly imported ids."""
        conn = self._import_connection()
        for table in ("jobs", "job_status_history"):
            await conn.execute(
                f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), "
                f"(SELECT COALESCE(MAX(id), 0) + 1 FROM {table}), false)"
            )

    async def bulk_insert_jobs(self, rows: list[JobRow]) -> int:
        """Insert job rows in bulk (legacy timestamps cast text -> timestamptz).

        Args:
            rows: Job rows to insert.

        Returns:
            Count of rows inserted.
        """
        if not rows:
            return 0
        conn = self._import_connection()
        await conn.executemany(
            """INSERT INTO jobs (
                id, platform, canonical_id, url, title, company, location,
                jd_text, jd_quality, posted_at, discovered_at, enriched_at,
                enrich_source, company_norm, title_norm, location_norm,
                jd_lang, enrich_error, quality_rubric_version, reapply_notice,
                hard_filter, seniority_level, degree_required, clearance_required,
                school_restricted, domain_tags, tech_required, role_type, yoe_min,
                ml_gate_score, ml_gate_result, ml_gate_fail_reason, ml_gate_at,
                ml_gate_version, is_swe_role
            ) VALUES (
                $1,$2,$3,$4,$5,$6,$7,$8,$9,$10::text::timestamptz,$11::text::timestamptz,
                $12::text::timestamptz,$13,$14,$15,$16,$17,$18,$19,$20,$21,$22,$23,
                $24,$25,$26,$27,$28,$29,$30,$31,$32,$33::text::timestamptz,$34,$35
            )""",
            [
                (
                    row["id"],
                    row["platform"],
                    row["canonical_id"],
                    row["url"],
                    row["title"],
                    row["company"],
                    row["location"],
                    row.get("jd_text"),
                    row.get("jd_quality"),
                    row.get("posted_at"),
                    row["discovered_at"],
                    row.get("enriched_at"),
                    row.get("enrich_source"),
                    row.get("company_norm"),
                    row.get("title_norm"),
                    row.get("location_norm"),
                    row.get("jd_lang"),
                    row.get("enrich_error"),
                    row.get("quality_rubric_version"),
                    row.get("reapply_notice"),
                    row.get("hard_filter"),
                    row.get("seniority_level"),
                    row.get("degree_required"),
                    row.get("clearance_required"),
                    row.get("school_restricted"),
                    row.get("domain_tags"),
                    row.get("tech_required"),
                    row.get("role_type"),
                    row.get("yoe_min"),
                    row.get("ml_gate_score"),
                    row.get("ml_gate_result"),
                    row.get("ml_gate_fail_reason"),
                    row.get("ml_gate_at"),
                    row.get("ml_gate_version"),
                    row.get("is_swe_role"),
                )
                for row in rows
            ],
        )
        return len(rows)

    async def bulk_insert_evaluations(self, rows: list[EvaluationRow]) -> int:
        """Insert evaluation rows in bulk (id auto-generated).

        Args:
            rows: Evaluation rows to insert.

        Returns:
            Count of rows inserted.
        """
        if not rows:
            return 0
        conn = self._import_connection()
        await conn.executemany(
            """INSERT INTO evaluations (
                job_id, stage_a_score, stage_a_one_line, stage_a_timing_eligible,
                stage_a_status, stage_a_error, stage_a_model, stage_a_cost_usd,
                stage_a_prompt_hash, stage_a_resume_hash,
                stage_b_verdict, stage_b_jd_summary,
                stage_b_verdict_json, stage_b_summary_json,
                stage_b_fit_json, stage_b_hooks_json,
                stage_b_status, stage_b_error, stage_b_model, stage_b_cost_usd,
                stage_b_prompt_hash, stage_b_resume_hash, created_at, updated_at
            ) VALUES (
                $1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,
                $13::jsonb,$14::jsonb,$15::jsonb,$16::jsonb,
                $17,$18,$19,$20,$21,$22,$23::text::timestamptz,$24::text::timestamptz
            )""",
            [
                (
                    row["job_id"],
                    row.get("stage_a_score"),
                    row.get("stage_a_one_line"),
                    row.get("stage_a_timing_eligible"),
                    row.get("stage_a_status"),
                    row.get("stage_a_error"),
                    row.get("stage_a_model"),
                    row.get("stage_a_cost_usd"),
                    row.get("stage_a_prompt_hash"),
                    row.get("stage_a_resume_hash"),
                    row.get("stage_b_verdict"),
                    row.get("stage_b_jd_summary"),
                    row.get("stage_b_verdict_json"),
                    row.get("stage_b_summary_json"),
                    row.get("stage_b_fit_json"),
                    row.get("stage_b_hooks_json"),
                    row.get("stage_b_status"),
                    row.get("stage_b_error"),
                    row.get("stage_b_model"),
                    row.get("stage_b_cost_usd"),
                    row.get("stage_b_prompt_hash"),
                    row.get("stage_b_resume_hash"),
                    row["created_at"],
                    row["updated_at"],
                )
                for row in rows
            ],
        )
        return len(rows)

    async def bulk_insert_job_status(self, rows: list[JobStatusRow]) -> int:
        """Insert job_status rows in bulk.

        Args:
            rows: Job status rows to insert.

        Returns:
            Count of rows inserted.
        """
        if not rows:
            return 0
        conn = self._import_connection()
        await conn.executemany(
            """INSERT INTO job_status (
                job_id, status, next_followup_at, resume_variant, notes,
                last_status_change_at
            ) VALUES ($1,$2,$3::text::timestamptz,$4,$5,$6::text::timestamptz)""",
            [
                (
                    row["job_id"],
                    row["status"],
                    row.get("next_followup_at"),
                    row.get("resume_variant"),
                    row.get("notes"),
                    row["last_status_change_at"],
                )
                for row in rows
            ],
        )
        return len(rows)

    async def bulk_insert_job_status_history(self, rows: list[StatusHistoryRow]) -> int:
        """Insert job_status_history rows in bulk with preserved ids.

        Args:
            rows: Status history rows to insert.

        Returns:
            Count of rows inserted.
        """
        if not rows:
            return 0
        conn = self._import_connection()
        await conn.executemany(
            """INSERT INTO job_status_history (
                id, job_id, from_status, to_status, changed_at, reason,
                resume_variant_at_change
            ) VALUES ($1,$2,$3,$4,$5::text::timestamptz,$6,$7)""",
            [
                (
                    row["id"],
                    row["job_id"],
                    row.get("from_status"),
                    row["to_status"],
                    row["changed_at"],
                    row.get("reason"),
                    row.get("resume_variant_at_change"),
                )
                for row in rows
            ],
        )
        return len(rows)

    async def bulk_insert_applied(self, rows: list[AppliedRow]) -> int:
        """Insert applied rows in bulk.

        Args:
            rows: Applied rows to insert.

        Returns:
            Count of rows inserted.
        """
        if not rows:
            return 0
        conn = self._import_connection()
        await conn.executemany(
            """INSERT INTO applied (
                job_id, applied_at, notes, master_resume_hash,
                tailored_resume_hash, cover_letter, application_method,
                verdict_snapshot, fit_snapshot, hooks_snapshot
            ) VALUES ($1,$2::text::timestamptz,$3,$4,$5,$6,$7,$8,$9,$10)""",
            [
                (
                    row["job_id"],
                    row["applied_at"],
                    row.get("notes"),
                    row.get("master_resume_hash"),
                    row.get("tailored_resume_hash"),
                    row.get("cover_letter"),
                    row.get("application_method"),
                    row.get("verdict_snapshot"),
                    row.get("fit_snapshot"),
                    row.get("hooks_snapshot"),
                )
                for row in rows
            ],
        )
        return len(rows)

    async def bulk_insert_resume_snapshots(self, rows: list[ResumeSnapshotRow]) -> int:
        """Insert resume_snapshots rows in bulk.

        Args:
            rows: Resume snapshot rows to insert.

        Returns:
            Count of rows inserted.
        """
        if not rows:
            return 0
        conn = self._import_connection()
        await conn.executemany(
            """INSERT INTO resume_snapshots (
                resume_hash, captured_at, source, content, notes
            ) VALUES ($1,$2::text::timestamptz,$3,$4,$5)""",
            [
                (
                    row["resume_hash"],
                    row["captured_at"],
                    row["source"],
                    row["content"],
                    row.get("notes"),
                )
                for row in rows
            ],
        )
        return len(rows)

    async def bulk_insert_resume_variants(self, rows: list[ResumeVariantRow]) -> int:
        """Insert resume_variants rows in bulk.

        Args:
            rows: Resume variant rows to insert.

        Returns:
            Count of rows inserted.
        """
        if not rows:
            return 0
        conn = self._import_connection()
        await conn.executemany(
            """INSERT INTO resume_variants (name, description, created_at)
               VALUES ($1,$2,$3::text::timestamptz)""",
            [(row["name"], row.get("description"), row["created_at"]) for row in rows],
        )
        return len(rows)

    async def bulk_insert_companies(self, rows: list[CompanyRow]) -> int:
        """Insert companies rows in bulk.

        Args:
            rows: Company rows to insert.

        Returns:
            Count of rows inserted.
        """
        if not rows:
            return 0
        conn = self._import_connection()
        await conn.executemany(
            """INSERT INTO companies (
                slug, ats_vendor, ats_override, last_verified_at,
                last_probe_attempt_at, job_count_last_scan, notes,
                consecutive_discover_failures
            ) VALUES ($1,$2,$3,$4::text::timestamptz,$5::text::timestamptz,$6,$7,$8)""",
            [
                (
                    row["slug"],
                    row.get("ats_vendor"),
                    row.get("ats_override", 0),
                    row.get("last_verified_at"),
                    row.get("last_probe_attempt_at"),
                    row.get("job_count_last_scan", 0),
                    row.get("notes"),
                    row.get("consecutive_discover_failures", 0),
                )
                for row in rows
            ],
        )
        return len(rows)

    async def bulk_insert_cost_ledger(self, rows: list[CostLedgerRow]) -> int:
        """Insert cost_ledger rows in bulk.

        Args:
            rows: Cost ledger rows to insert.

        Returns:
            Count of rows inserted.
        """
        if not rows:
            return 0
        conn = self._import_connection()
        await conn.executemany(
            """INSERT INTO cost_ledger (day, spent_usd, calls, last_updated)
               VALUES ($1,$2,$3,$4::text::timestamptz)""",
            [
                (
                    row["day"],
                    row["spent_usd"],
                    row.get("calls", 0),
                    row["last_updated"],
                )
                for row in rows
            ],
        )
        return len(rows)

    async def bulk_insert_state(self, rows: list[StateRow]) -> int:
        """Insert state rows in bulk (upsert on key).

        Args:
            rows: State rows to insert.

        Returns:
            Count of rows inserted.
        """
        if not rows:
            return 0
        conn = self._import_connection()
        await conn.executemany(
            """INSERT INTO state (key, value) VALUES ($1,$2)
               ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value""",
            [(row["key"], row["value"]) for row in rows],
        )
        return len(rows)

    async def read_all_rows(self, table: str) -> list[dict[str, Any]]:
        """Read all rows from a table as dicts for parity verification.

        Args:
            table: Table name to read from.

        Returns:
            List of row dicts.

        Raises:
            ValueError: If table name is not in the allowlist.
        """
        if table not in self._PARITY_TABLES:
            raise ValueError(f"Table {table!r} not allowed for parity reads")
        async with self._get_pool().acquire() as conn:
            records = await conn.fetch(f"SELECT * FROM {table}")
        return [dict(r) for r in records]

    async def count_rows(self, table: str) -> int:
        """Count rows in a table for parity verification.

        Args:
            table: Table name to count rows in.

        Returns:
            Row count.

        Raises:
            ValueError: If table name is not in the allowlist.
        """
        if table not in self._PARITY_TABLES:
            raise ValueError(f"Table {table!r} not allowed for parity reads")
        async with self._get_pool().acquire() as conn:
            value = await conn.fetchval(f"SELECT COUNT(*) FROM {table}")
        return int(value) if value is not None else 0

    # ------------------------------------------------------------------
    # JobStore core methods
    # ------------------------------------------------------------------

    async def save_job(self, job: JobPosting) -> SaveJobResult:
        """Insert or upsert a job by (platform, canonical_id).

        Uses ``INSERT ... ON CONFLICT DO UPDATE ... RETURNING id, (xmax = 0) AS inserted``
        to atomically handle the upsert in a single round-trip.

        Args:
            job: Job posting to persist.

        Returns:
            Upsert result with store-assigned identity.
        """
        pool = self._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""INSERT INTO jobs (
                       platform, canonical_id, url, title, company, location,
                       jd_text, jd_quality, posted_at, discovered_at,
                       enriched_at, enrich_source,
                       company_norm, title_norm, location_norm,
                       closed_at, enrich_error
                   ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17)
                   ON CONFLICT (platform, canonical_id) DO UPDATE SET
                       url = EXCLUDED.url,
                       title = EXCLUDED.title,
                       company = EXCLUDED.company,
                       location = EXCLUDED.location,
                       jd_text = CASE
                           WHEN $18::int >= (CASE jobs.jd_quality
                               WHEN 'full' THEN 5 WHEN 'good' THEN 4
                               WHEN 'partial' THEN 3 WHEN 'stub' THEN 2
                               WHEN 'missing' THEN 1 WHEN 'abandoned' THEN 0
                               ELSE -1 END)
                           THEN COALESCE(EXCLUDED.jd_text, jobs.jd_text)
                           ELSE jobs.jd_text END,
                       jd_quality = CASE
                           WHEN $18::int >= (CASE jobs.jd_quality
                               WHEN 'full' THEN 5 WHEN 'good' THEN 4
                               WHEN 'partial' THEN 3 WHEN 'stub' THEN 2
                               WHEN 'missing' THEN 1 WHEN 'abandoned' THEN 0
                               ELSE -1 END)
                           THEN COALESCE(EXCLUDED.jd_quality, jobs.jd_quality)
                           ELSE jobs.jd_quality END,
                       posted_at = COALESCE(EXCLUDED.posted_at, jobs.posted_at),
                       discovered_at = EXCLUDED.discovered_at,
                       enriched_at = COALESCE(EXCLUDED.enriched_at, jobs.enriched_at),
                       -- enrich_source must track whichever jd_text actually
                       -- wins above: only adopt the incoming provenance when the
                       -- incoming JD is kept, else a no-JD/lower-quality re-scan
                       -- (e.g. a transient vendor failure) would relabel a
                       -- preserved full JD as speedyapply-error/-unrouted.
                       enrich_source = CASE
                           WHEN $18::int >= (CASE jobs.jd_quality
                               WHEN 'full' THEN 5 WHEN 'good' THEN 4
                               WHEN 'partial' THEN 3 WHEN 'stub' THEN 2
                               WHEN 'missing' THEN 1 WHEN 'abandoned' THEN 0
                               ELSE -1 END)
                           THEN COALESCE(EXCLUDED.enrich_source, jobs.enrich_source)
                           ELSE jobs.enrich_source END,
                       company_norm = EXCLUDED.company_norm,
                       title_norm = EXCLUDED.title_norm,
                       location_norm = EXCLUDED.location_norm,
                       closed_at = CASE WHEN EXCLUDED.jd_text IS NOT NULL THEN NULL
                                        ELSE COALESCE(jobs.closed_at, EXCLUDED.closed_at) END,
                       enrich_error = CASE WHEN EXCLUDED.jd_text IS NOT NULL THEN NULL
                                           ELSE COALESCE(EXCLUDED.enrich_error, jobs.enrich_error) END,
                       -- Reset the stale ML-gate verdict iff a gate INPUT
                       -- feature (title, or the JD that actually WINS this
                       -- upsert) changed. A stale 'fail' is excluded forever
                       -- (ml_gate_result IS DISTINCT FROM 'fail') and a stale
                       -- 'pass' is trusted without re-gating, so on a content
                       -- change we NULL the verdict columns to force a re-gate.
                       -- _PG_GATE_INPUT_CHANGED reuses the exact winning-jd_text
                       -- CASE from the jd_text assignment above: a lower-quality
                       -- / no-JD re-scan that loses keeps the stored JD, so the
                       -- input is unchanged and the verdict is preserved
                       -- (Decision 8: don't needlessly re-gate an identical
                       -- re-scan and risk a model-drift flip).
                       {_PG_RESET_ML_GATE_SET}
                   RETURNING id, (xmax = 0) AS inserted""",
                job.platform,
                job.canonical_id,
                job.url,
                job.title,
                job.company,
                job.location,
                job.jd_text,
                job.jd_quality.value if job.jd_quality else None,
                job.posted_at,
                job.discovered_at,
                job.enriched_at,
                job.enrich_source,
                normalize_company(job.company),
                normalize(job.title),
                normalize(job.location),
                job.closed_at,
                job.enrich_error,
                quality_rank(job.jd_quality),
            )
            assert row is not None  # RETURNING always produces a row
            inserted = bool(row["inserted"])
            return SaveJobResult(
                job_id=str(row["id"]),
                inserted=inserted,
                updated=not inserted,
            )

    async def get_job(self, job_id: str) -> JobPosting | None:
        """Load a job by store identity.

        Args:
            job_id: Store-assigned identity.

        Returns:
            Job posting if found, else None.
        """
        pool = self._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM jobs WHERE id = $1", int(job_id))
        return _job_from_record(row) if row is not None else None

    async def list_jobs(self, limit: int = 100) -> list[JobPosting]:
        """List recent jobs.

        Args:
            limit: Max jobs.

        Returns:
            Job postings by recency.
        """
        pool = self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM jobs ORDER BY discovered_at DESC, id DESC LIMIT $1",
                limit,
            )
        return [_job_from_record(r) for r in rows]

    async def job_exists(self, *, platform: str, canonical_id: str) -> bool:
        """Check job existence by natural key.

        Args:
            platform: Source platform.
            canonical_id: Platform-specific identity.

        Returns:
            True if the job exists.
        """
        pool = self._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT 1 FROM jobs WHERE platform = $1 AND canonical_id = $2",
                platform,
                canonical_id,
            )
        return row is not None

    async def get_enrichment(
        self,
        *,
        platform: str,
        canonical_id: str,
    ) -> StoredEnrichment | None:
        """Return the stored enrichment snapshot for a job (EnrichmentLookup port).

        Args:
            platform: Source platform.
            canonical_id: Platform-specific identity.

        Returns:
            Stored enrichment snapshot, or None when the job is unknown.
        """
        pool = self._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """SELECT jd_text, jd_quality, enriched_at, enrich_source
                   FROM jobs WHERE platform = $1 AND canonical_id = $2""",
                platform,
                canonical_id,
            )
        if row is None:
            return None
        return StoredEnrichment(
            jd_text=row["jd_text"],
            quality=QualityBand(row["jd_quality"]) if row["jd_quality"] else None,
            enriched_at=row["enriched_at"],
            enrich_source=row["enrich_source"],
        )

    async def get_closed_canonical_ids(self, *, platform: str) -> set[str]:
        """Return canonical ids of definitively closed jobs (ClosedJobLookup).

        Heuristic ``mark-stale-closed`` backfill closures are excluded: they
        are guesses, not proven-gone postings, and the save-path self-heal can
        only clear their ``closed_at`` if a later JD fetch succeeds — which
        requires the source to keep re-fetching them.

        Args:
            platform: Source platform to scope the lookup to.

        Returns:
            The set of ``canonical_id`` values whose row has ``closed_at`` set
            and is not a heuristic stale-backfill closure.
        """
        pool = self._get_pool()
        async with pool.acquire() as conn:
            # Heuristic backfill closures stay re-fetchable: the save-path
            # self-heal needs a successful fetch to recover a live posting.
            rows = await conn.fetch(
                """SELECT canonical_id FROM jobs
                   WHERE platform = $1 AND closed_at IS NOT NULL
                     AND enrich_error IS DISTINCT FROM $2""",
                platform,
                _STALE_BACKFILL_MARKER,
            )
        return {row["canonical_id"] for row in rows}

    async def save_stage_a(self, job_id: str, result: StageAResult) -> None:
        """Persist a successful Stage A result and advance status to scored.

        The evaluation upsert and the ``new -> scored`` status advance commit
        atomically. The advance only fires when the job is still ``new``, so
        re-evaluation and jobs already moved along the workflow are untouched.

        Args:
            job_id: Store-assigned identity.
            result: Stage A result.
        """
        pool = self._get_pool()
        async with pool.acquire() as conn, conn.transaction():
            await conn.execute(
                """INSERT INTO evaluations (
                       job_id, stage_a_score, stage_a_one_line,
                       stage_a_timing_eligible, stage_a_status, stage_a_error,
                       stage_a_model, stage_a_cost_usd,
                       stage_a_prompt_hash, stage_a_resume_hash, stage_a_at
                   ) VALUES ($1,$2,$3,$4,'completed',NULL,$5,$6,$7,$8,now())
                   ON CONFLICT (job_id) DO UPDATE SET
                       stage_a_score = EXCLUDED.stage_a_score,
                       stage_a_one_line = EXCLUDED.stage_a_one_line,
                       stage_a_timing_eligible = EXCLUDED.stage_a_timing_eligible,
                       stage_a_status = EXCLUDED.stage_a_status,
                       stage_a_error = NULL,
                       stage_a_model = EXCLUDED.stage_a_model,
                       stage_a_cost_usd = EXCLUDED.stage_a_cost_usd,
                       stage_a_prompt_hash = EXCLUDED.stage_a_prompt_hash,
                       stage_a_resume_hash = EXCLUDED.stage_a_resume_hash,
                       stage_a_at = COALESCE(evaluations.stage_a_at, now()),
                       stage_b_status = CASE
                           WHEN evaluations.stage_b_status = 'skipped_below_threshold'
                               THEN NULL
                           ELSE evaluations.stage_b_status
                       END,
                       stage_b_error = CASE
                           WHEN evaluations.stage_b_status = 'skipped_below_threshold'
                               THEN NULL
                           ELSE evaluations.stage_b_error
                       END,
                       updated_at = now()""",
                int(job_id),
                result.score,
                result.one_line,
                result.timing_eligible,
                result.model,
                result.cost_usd,
                result.prompt_hash,
                result.resume_hash,
            )
            row = await conn.fetchrow(
                "SELECT status FROM job_status WHERE job_id = $1 FOR UPDATE",
                int(job_id),
            )
            if row is not None and row["status"] == "new":
                await self._transition_status_in_tx(
                    conn,
                    TransitionRequest(
                        job_id=job_id,
                        new_status="scored",
                        reason=REASON_AUTO_SCORED,
                    ),
                )

    async def save_stage_a_error(self, job_id: str, error: str) -> None:
        """Record a Stage A error (retryable).

        Args:
            job_id: Store-assigned identity.
            error: Error detail.
        """
        pool = self._get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO evaluations (
                       job_id, stage_a_status, stage_a_error, stage_a_error_count
                   ) VALUES ($1, 'error', $2, 1)
                   ON CONFLICT (job_id) DO UPDATE SET
                       stage_a_status = EXCLUDED.stage_a_status,
                       stage_a_error = EXCLUDED.stage_a_error,
                       stage_a_error_count = evaluations.stage_a_error_count + 1,
                       updated_at = now()""",
                int(job_id),
                error,
            )

    async def save_stage_b(self, job_id: str, result: StageBResult) -> None:
        """Persist a successful Stage B result.

        Args:
            job_id: Store-assigned identity.
            result: Stage B result.
        """
        pool = self._get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO evaluations (
                       job_id, stage_b_verdict, stage_b_jd_summary,
                       stage_b_verdict_json, stage_b_summary_json,
                       stage_b_fit_json, stage_b_hooks_json,
                       stage_b_status, stage_b_error,
                       stage_b_model, stage_b_cost_usd,
                       stage_b_prompt_hash, stage_b_resume_hash, stage_b_at
                   ) VALUES ($1,$2,$3,$4::jsonb,$5::jsonb,$6::jsonb,$7::jsonb,'completed',NULL,$8,$9,$10,$11,now())
                   ON CONFLICT (job_id) DO UPDATE SET
                       stage_b_verdict = EXCLUDED.stage_b_verdict,
                       stage_b_jd_summary = EXCLUDED.stage_b_jd_summary,
                       stage_b_verdict_json = EXCLUDED.stage_b_verdict_json,
                       stage_b_summary_json = EXCLUDED.stage_b_summary_json,
                       stage_b_fit_json = EXCLUDED.stage_b_fit_json,
                       stage_b_hooks_json = EXCLUDED.stage_b_hooks_json,
                       stage_b_status = EXCLUDED.stage_b_status,
                       stage_b_error = NULL,
                       stage_b_model = EXCLUDED.stage_b_model,
                       stage_b_cost_usd = EXCLUDED.stage_b_cost_usd,
                       stage_b_prompt_hash = EXCLUDED.stage_b_prompt_hash,
                       stage_b_resume_hash = EXCLUDED.stage_b_resume_hash,
                       stage_b_at = COALESCE(evaluations.stage_b_at, now()),
                       updated_at = now()""",
                int(job_id),
                result.verdict.value,
                result.jd_summary,
                _stage_b_block_json(result, "verdict"),
                _stage_b_block_json(result, "jd_summary"),
                _stage_b_block_json(result, "fit_analysis"),
                _stage_b_block_json(result, "resume_hooks"),
                result.model,
                result.cost_usd,
                result.prompt_hash,
                result.resume_hash,
            )

    async def save_stage_b_error(self, job_id: str, error: str) -> None:
        """Record a Stage B error (retryable).

        Args:
            job_id: Store-assigned identity.
            error: Error detail.
        """
        pool = self._get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO evaluations (
                       job_id, stage_b_status, stage_b_error, stage_b_error_count
                   ) VALUES ($1, 'error', $2, 1)
                   ON CONFLICT (job_id) DO UPDATE SET
                       stage_b_status = EXCLUDED.stage_b_status,
                       stage_b_error = EXCLUDED.stage_b_error,
                       stage_b_error_count = evaluations.stage_b_error_count + 1,
                       updated_at = now()""",
                int(job_id),
                error,
            )

    async def mark_stage_b_skipped(self, job_id: str) -> None:
        """Mark Stage B as skipped (threshold decision by service).

        Args:
            job_id: Store-assigned identity.
        """
        pool = self._get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """UPDATE evaluations SET
                       stage_b_status = 'skipped_below_threshold',
                       updated_at = now()
                   WHERE job_id = $1
                     AND stage_b_status IS DISTINCT FROM 'completed'""",
                int(job_id),
            )

    async def mark_stage_b_skipped_batch(self, job_ids: list[str]) -> None:
        """Mark multiple Stage B evaluations as skipped in a single UPDATE.

        Args:
            job_ids: Store-assigned job identities to skip.
        """
        if not job_ids:
            return
        int_ids = [int(jid) for jid in job_ids]
        pool = self._get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """UPDATE evaluations SET
                       stage_b_status = 'skipped_below_threshold',
                       updated_at = now()
                   WHERE job_id = ANY($1)
                     AND stage_b_status IS DISTINCT FROM 'completed'""",
                int_ids,
            )

    async def mark_stage_b_below_threshold(
        self,
        threshold: int,
        *,
        max_days: int | None = None,
    ) -> int:
        """Mark pending Stage B rows below the Stage A threshold.

        Args:
            threshold: Minimum Stage A score allowed into Stage B.
            max_days: Optional freshness window on discovered_at.

        Returns:
            Number of rows marked skipped.
        """
        conditions = [
            "evaluations.job_id = jobs.id",
            "evaluations.stage_a_status = 'completed'",
            """(evaluations.stage_b_status IS NULL
               OR evaluations.stage_b_status = 'error'
               OR (
                   evaluations.stage_b_status = 'in_progress'
                   AND evaluations.stage_b_verdict IS NULL
                   AND evaluations.updated_at < $2
               ))""",
            _PG_STAGE_B_UNDER_CAP,
            "evaluations.stage_a_score < $1",
        ]
        params: list[Any] = [threshold, datetime.now(UTC) - _PG_EVALUATION_CLAIM_TTL]
        if max_days is not None:
            params.append(datetime.now(UTC) - timedelta(days=max_days))
            conditions.append(f"jobs.discovered_at >= ${len(params)}")
        where = " AND ".join(conditions)
        sql = (
            "UPDATE evaluations SET "
            "stage_b_status = 'skipped_below_threshold', updated_at = now() "
            f"FROM jobs WHERE {where} RETURNING evaluations.job_id"
        )
        pool = self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(sql, *params)
        return len(rows)

    async def reopen_stage_b_at_or_above_threshold(
        self,
        threshold: int,
        *,
        max_days: int | None = None,
    ) -> int:
        """Reopen threshold-skipped Stage B rows that meet the active threshold.

        Args:
            threshold: Minimum Stage A score allowed into Stage B.
            max_days: Optional freshness window on discovered_at.

        Returns:
            Number of rows reopened.
        """
        conditions = [
            "evaluations.job_id = jobs.id",
            "evaluations.stage_a_status = 'completed'",
            "evaluations.stage_b_status = 'skipped_below_threshold'",
            "evaluations.stage_a_score >= $1",
        ]
        params: list[Any] = [threshold]
        if max_days is not None:
            params.append(datetime.now(UTC) - timedelta(days=max_days))
            conditions.append(f"jobs.discovered_at >= ${len(params)}")
        sql = (
            "UPDATE evaluations SET stage_b_status = NULL, "
            "stage_b_error = NULL, updated_at = now() "
            f"FROM jobs WHERE {' AND '.join(conditions)} "
            "RETURNING evaluations.job_id"
        )
        pool = self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(sql, *params)
        return len(rows)

    async def load_pending_stage_a(
        self,
        *,
        limit: int = 100,
        quality_bands: frozenset[str] | None = None,
        corpus: str = "unrated",
        max_days: int | None = None,
    ) -> list[JobPosting]:
        """Load jobs pending Stage A evaluation.

        Args:
            limit: Max jobs.
            quality_bands: Filter by jd_quality.
            corpus: "unrated", "all", or "failed".
            max_days: Freshness filter on discovered_at.

        Returns:
            Jobs pending Stage A.
        """
        sql, params = _build_pending_stage_a_query(
            limit=limit,
            quality_bands=quality_bands,
            corpus=corpus,
            max_days=max_days,
            now=datetime.now(UTC),
        )
        pool = self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(sql, *params)
        return [_job_from_record(r) for r in rows]

    async def claim_pending_stage_a(
        self,
        *,
        limit: int = 100,
        quality_bands: frozenset[str] | None = None,
        corpus: str = "unrated",
        max_days: int | None = None,
    ) -> list[JobPosting]:
        """Atomically claim jobs pending Stage A evaluation.

        Args:
            limit: Max jobs.
            quality_bands: Filter by jd_quality.
            corpus: "unrated", "all", or "failed".
            max_days: Freshness filter on discovered_at.

        Returns:
            Jobs claimed for this Stage A run.
        """
        sql, params = _build_claim_stage_a_query(
            limit=limit,
            quality_bands=quality_bands,
            corpus=corpus,
            max_days=max_days,
            now=datetime.now(UTC),
        )
        pool = self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(sql, *params)
        return [_job_from_record(r) for r in rows]

    async def preview_claimable_stage_a(
        self,
        *,
        limit: int = 100,
        quality_bands: frozenset[str] | None = None,
        corpus: str = "unrated",
        max_days: int | None = None,
    ) -> list[JobPosting]:
        """Preview jobs claimable for Stage A without mutating leases.

        Args:
            limit: Max jobs.
            quality_bands: Filter by jd_quality.
            corpus: "unrated", "all", or "failed".
            max_days: Freshness filter on discovered_at.

        Returns:
            Jobs a real Stage A run would claim.
        """
        sql, params = _build_claimable_stage_a_query(
            limit=limit,
            quality_bands=quality_bands,
            corpus=corpus,
            max_days=max_days,
            now=datetime.now(UTC),
        )
        pool = self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(sql, *params)
        return [_job_from_record(r) for r in rows]

    async def load_gate_candidates(
        self,
        *,
        corpus: str = "unrated",
        quality_bands: frozenset[str] | None = None,
        max_days: int | None = None,
        limit: int = 100,
        exclude_gate_failed: bool = True,
        after: tuple[datetime, int] | None = None,
    ) -> list[GateCandidate]:
        """Load ML-gate candidates pending Stage A (NON-claiming read).

        Reuses the shared Stage A eligibility filters plus the same
        stale-takeover corpus predicate the claim builders use (so a stale
        ``in_progress`` row past the claim TTL re-enters under "unrated"), and
        adds the not-yet-Stage-A-scored predicate plus, when
        ``exclude_gate_failed``, an ``ml_gate_result IS DISTINCT FROM 'fail'``
        guard. Includes rows with a NULL or 'pass' gate that are not yet
        Stage-A-scored (crash recovery) and excludes already-Stage-A-scored rows.
        Each row carries its persisted ``ml_gate_result``. Writes nothing.

        ``after`` is an optional ``(discovered_at, id)`` keyset cursor: when set,
        only rows strictly past it in ``discovered_at DESC, id DESC`` order are
        returned. The funnel pages with it to overfetch past hard-filtered drops
        so an eligible older row is not starved by a blocked newest prefix.

        Args:
            corpus: "unrated", "all", or "failed".
            quality_bands: Filter by jd_quality.
            max_days: Freshness filter on discovered_at.
            limit: Max jobs (one page).
            exclude_gate_failed: When True, drop rows whose gate result is 'fail';
                when False, gate state is ignored entirely.
            after: Optional ``(discovered_at, id)`` keyset cursor for the next page.

        Returns:
            Gate candidates (job + persisted ``ml_gate_result``) pending Stage A,
            with no lease mutation.
        """
        sql, params = _build_gate_candidates_query(
            limit=limit,
            quality_bands=quality_bands,
            corpus=corpus,
            max_days=max_days,
            exclude_gate_failed=exclude_gate_failed,
            now=datetime.now(UTC),
            after=after,
        )
        pool = self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(sql, *params)
        return [_gate_candidate_from_record(r) for r in rows]

    async def claim_stage_a_by_ids(
        self,
        job_ids: list[str],
        *,
        quality_bands: frozenset[str] | None = None,
        corpus: str = "unrated",
        max_days: int | None = None,
        limit: int = 100,
    ) -> list[JobPosting]:
        """Atomically claim Stage A jobs restricted to an explicit id set.

        Same claim machinery as ``claim_pending_stage_a`` (``SKIP LOCKED`` +
        ``in_progress`` upsert with stale-takeover / retry-cap semantics), but
        the candidate scan is constrained to ``job_ids``. Store ids are
        bigint-as-str; non-numeric ids match no row and are dropped, so an empty
        or all-non-numeric ``job_ids`` returns ``[]`` and writes nothing.

        Args:
            job_ids: Store-assigned identities to claim from.
            quality_bands: Filter by jd_quality.
            corpus: "unrated", "all", or "failed".
            max_days: Freshness filter on discovered_at.
            limit: Max jobs.

        Returns:
            Jobs claimed for this Stage A run (subset of ``job_ids``).
        """
        numeric_ids = _numeric_job_ids(job_ids)
        if not numeric_ids:
            return []
        sql, params = _build_claim_stage_a_by_ids_query(
            job_ids=numeric_ids,
            limit=limit,
            quality_bands=quality_bands,
            corpus=corpus,
            max_days=max_days,
            now=datetime.now(UTC),
        )
        pool = self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(sql, *params)
        return [_job_from_record(r) for r in rows]

    async def load_pending_stage_b(
        self,
        *,
        limit: int = 100,
        max_days: int | None = None,
        stage_a_threshold: int | None = None,
    ) -> list[JobPosting]:
        """Load Stage A-completed, Stage B-pending jobs.

        Args:
            limit: Max jobs.
            max_days: Freshness filter.
            stage_a_threshold: Optional minimum Stage A score.

        Returns:
            Jobs pending Stage B.
        """
        sql, params = _build_pending_stage_b_query(
            limit=limit,
            max_days=max_days,
            stage_a_threshold=stage_a_threshold,
            now=datetime.now(UTC),
        )
        pool = self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(sql, *params)
        return [_job_from_record(r) for r in rows]

    async def claim_pending_stage_b(
        self,
        *,
        limit: int = 100,
        max_days: int | None = None,
        stage_a_threshold: int | None = None,
    ) -> list[JobPosting]:
        """Atomically claim jobs pending Stage B evaluation.

        Args:
            limit: Max jobs.
            max_days: Freshness filter.
            stage_a_threshold: Optional minimum Stage A score.

        Returns:
            Jobs claimed for this Stage B run.
        """
        sql, params = _build_claim_stage_b_query(
            limit=limit,
            max_days=max_days,
            stage_a_threshold=stage_a_threshold,
            now=datetime.now(UTC),
        )
        pool = self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(sql, *params)
        return [_job_from_record(r) for r in rows]

    async def release_stage_a_claim(self, job_id: str) -> None:
        """Release an unspent Stage A in-progress claim.

        Args:
            job_id: Store-assigned identity.
        """
        pool = self._get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """UPDATE evaluations
                   SET stage_a_status = CASE
                           WHEN stage_a_error IS NOT NULL THEN 'error'
                           WHEN stage_a_score IS NOT NULL THEN 'completed'
                           ELSE NULL
                       END,
                       updated_at = now()
                 WHERE job_id = $1 AND stage_a_status = 'in_progress'""",
                int(job_id),
            )

    async def release_stage_b_claim(self, job_id: str) -> None:
        """Release an unspent Stage B in-progress claim.

        Args:
            job_id: Store-assigned identity.
        """
        pool = self._get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """UPDATE evaluations
                   SET stage_b_status = CASE
                           WHEN stage_b_error IS NOT NULL THEN 'error'
                           WHEN stage_b_verdict IS NOT NULL THEN 'completed'
                           ELSE NULL
                       END,
                       updated_at = now()
                 WHERE job_id = $1 AND stage_b_status = 'in_progress'""",
                int(job_id),
            )

    async def refresh_stage_b_claim(self, job_id: str) -> None:
        """Refresh an active Stage B in-progress claim.

        Args:
            job_id: Store-assigned identity.
        """
        pool = self._get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """UPDATE evaluations
                      SET updated_at = now()
                    WHERE job_id = $1 AND stage_b_status = 'in_progress'""",
                int(job_id),
            )

    async def preview_pending_stage_b_after_threshold_sync(
        self,
        *,
        limit: int = 100,
        max_days: int | None = None,
        stage_a_threshold: int,
    ) -> list[JobPosting]:
        """Preview Stage B jobs after threshold sync without mutating rows.

        Args:
            limit: Max jobs.
            max_days: Freshness filter.
            stage_a_threshold: Active Stage A threshold.

        Returns:
            Jobs that a real Stage B run would consider after reopening
            eligible threshold-skipped rows and filtering below-threshold rows.
        """
        sql, params = _build_stage_b_preview_query(
            limit=limit,
            max_days=max_days,
            stage_a_threshold=stage_a_threshold,
            now=datetime.now(UTC),
        )
        pool = self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(sql, *params)
        return [_job_from_record(r) for r in rows]

    async def list_evaluated_jobs(self, limit: int = 100) -> list[JobEvaluation]:
        """List jobs with evaluations.

        Args:
            limit: Max evaluations.

        Returns:
            Joined evaluations.
        """
        pool = self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """SELECT
                       jobs.*,
                       evaluations.stage_a_score, evaluations.stage_a_one_line,
                       evaluations.stage_a_timing_eligible, evaluations.stage_a_status,
                       evaluations.stage_a_error, evaluations.stage_a_model,
                       evaluations.stage_a_cost_usd, evaluations.stage_a_prompt_hash,
                       evaluations.stage_a_resume_hash,
                       evaluations.stage_b_verdict, evaluations.stage_b_jd_summary,
                       evaluations.stage_b_verdict_json, evaluations.stage_b_summary_json,
                       evaluations.stage_b_fit_json, evaluations.stage_b_hooks_json,
                       evaluations.stage_b_status, evaluations.stage_b_error,
                       evaluations.stage_b_model, evaluations.stage_b_cost_usd,
                       evaluations.stage_b_prompt_hash, evaluations.stage_b_resume_hash
                   FROM jobs
                   JOIN evaluations ON evaluations.job_id = jobs.id
                   ORDER BY jobs.discovered_at DESC, jobs.id DESC
                   LIMIT $1""",
                limit,
            )
        return [_evaluation_from_record(r) for r in rows]

    async def get_evaluation(self, job_id: str) -> JobEvaluation | None:
        """Fetch a single job's evaluation.

        Args:
            job_id: Store-assigned identity.

        Returns:
            Evaluation if found, else None.
        """
        pool = self._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """SELECT
                       jobs.*,
                       evaluations.stage_a_score, evaluations.stage_a_one_line,
                       evaluations.stage_a_timing_eligible, evaluations.stage_a_status,
                       evaluations.stage_a_error, evaluations.stage_a_model,
                       evaluations.stage_a_cost_usd, evaluations.stage_a_prompt_hash,
                       evaluations.stage_a_resume_hash,
                       evaluations.stage_b_verdict, evaluations.stage_b_jd_summary,
                       evaluations.stage_b_verdict_json, evaluations.stage_b_summary_json,
                       evaluations.stage_b_fit_json, evaluations.stage_b_hooks_json,
                       evaluations.stage_b_status, evaluations.stage_b_error,
                       evaluations.stage_b_model, evaluations.stage_b_cost_usd,
                       evaluations.stage_b_prompt_hash, evaluations.stage_b_resume_hash
                   FROM jobs
                   LEFT JOIN evaluations ON evaluations.job_id = jobs.id
                   WHERE jobs.id = $1""",
                int(job_id),
            )
        if row is None:
            return None
        return _evaluation_from_record(row)

    async def get_stage_a_scores(
        self,
        job_ids: list[str],
    ) -> dict[str, int | None]:
        """Batch-fetch Stage A scores for the given jobs.

        Args:
            job_ids: Store-assigned job identities.

        Returns:
            Mapping of job_id → stage_a_score (None when unevaluated).
        """
        if not job_ids:
            return {}
        int_ids = [int(jid) for jid in job_ids]
        async with self._get_pool().acquire() as conn:
            rows = await conn.fetch(
                """SELECT job_id, stage_a_score
                   FROM evaluations
                   WHERE job_id = ANY($1)""",
                int_ids,
            )
        return {str(row["job_id"]): row["stage_a_score"] for row in rows}

    async def top_evaluated_jobs(
        self,
        *,
        min_score: int = 0,
        limit: int = 100,
    ) -> list[JobEvaluation]:
        """Stage B-completed jobs by score descending.

        Args:
            min_score: Score threshold.
            limit: Max jobs.

        Returns:
            Sorted evaluations.
        """
        pool = self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """SELECT
                       jobs.*,
                       evaluations.stage_a_score, evaluations.stage_a_one_line,
                       evaluations.stage_a_timing_eligible, evaluations.stage_a_status,
                       evaluations.stage_a_error, evaluations.stage_a_model,
                       evaluations.stage_a_cost_usd, evaluations.stage_a_prompt_hash,
                       evaluations.stage_a_resume_hash,
                       evaluations.stage_b_verdict, evaluations.stage_b_jd_summary,
                       evaluations.stage_b_verdict_json, evaluations.stage_b_summary_json,
                       evaluations.stage_b_fit_json, evaluations.stage_b_hooks_json,
                       evaluations.stage_b_status, evaluations.stage_b_error,
                       evaluations.stage_b_model, evaluations.stage_b_cost_usd,
                       evaluations.stage_b_prompt_hash, evaluations.stage_b_resume_hash
                   FROM jobs
                   JOIN evaluations ON evaluations.job_id = jobs.id
                   WHERE evaluations.stage_b_status = 'completed'
                     AND evaluations.stage_a_score >= $1
                   ORDER BY evaluations.stage_a_score DESC
                   LIMIT $2""",
                min_score,
                limit,
            )
        return [_evaluation_from_record(r) for r in rows]

    async def query_jobs_view(self, query: JobsViewQuery) -> JobsViewPage:
        """Run the filtered, paginated Phase 8 jobs view query.

        One SELECT over jobs LEFT JOIN evaluations LEFT JOIN job_status for
        the active tab's rows (ordered per ``query.sort``, bounded by
        limit/offset), plus the active tab's total and a one-row per-tab
        count aggregate. All request filters apply to the rows, the total,
        and every tab_counts bucket alike, so tab_counts[t] equals the total
        of re-running the same request with tab=t.

        Args:
            query: Tab, filters, sort, and pagination window.

        Returns:
            Bounded jobs view page.
        """
        shared, params = _jobs_view_filters(query)
        where = " AND ".join([_JOBS_VIEW_TAB_PREDICATES[query.tab], *shared])
        shared_where = " AND ".join(shared) if shared else "TRUE"
        rows_sql = (
            f"SELECT {_JOBS_VIEW_COLUMNS} {_JOBS_VIEW_FROM} WHERE {where}"
            f" ORDER BY {_JOBS_VIEW_SORT_SQL[query.sort]}"
            f" LIMIT ${len(params) + 1} OFFSET ${len(params) + 2}"
        )
        pool = self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(rows_sql, *params, query.limit, query.offset)
            total = await self._count(
                conn,
                f"SELECT COUNT(*) {_JOBS_VIEW_FROM} WHERE {where}",
                *params,
            )
            counts_row = await conn.fetchrow(
                _jobs_view_counts_sql(shared_where), *params
            )
        return JobsViewPage(
            rows=[_jobs_view_row_from_record(r) for r in rows],
            total=total,
            tab_counts={tab: int(counts_row[tab]) for tab in VALID_TABS},
        )

    async def list_twin_rows_by_status(
        self,
        keys: Sequence[tuple[str, str]],
        *,
        statuses: Sequence[str],
        limit: int,
    ) -> list[JobsViewRow]:
        """List view rows in the given statuses for the given twin keys.

        Feeds the display fold (plan D9): the tab-filtered jobs-view corpus
        never contains out-of-tab siblings, so the fold pulls them in
        explicitly (e.g. the in-flight ``applied`` twin of a queue row). The
        ``<> ''`` guards are NULL-safe, so blank-norm rows never match —
        mirroring ``list_twin_statuses``.

        Args:
            keys: Non-blank ``(company_norm, title_norm)`` cluster keys.
            statuses: Workflow statuses to keep.
            limit: Maximum rows returned.

        Returns:
            Matching view rows, newest discovered first.
        """
        if not keys or not statuses:
            return []
        sql = (
            f"SELECT {_JOBS_VIEW_COLUMNS} {_JOBS_VIEW_FROM}"
            " JOIN unnest($1::text[], $2::text[])"
            "   AS twin_keys(company_norm, title_norm)"
            "   ON jobs.company_norm = twin_keys.company_norm"
            "  AND jobs.title_norm = twin_keys.title_norm"
            " WHERE job_status.status = ANY($3::text[])"
            "   AND jobs.company_norm <> ''"
            "   AND jobs.title_norm <> ''"
            " ORDER BY jobs.discovered_at DESC, jobs.id DESC"
            " LIMIT $4"
        )
        pool = self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                sql,
                [key[0] for key in keys],
                [key[1] for key in keys],
                list(statuses),
                limit,
            )
        return [_jobs_view_row_from_record(r) for r in rows]

    async def list_twin_statuses(self, job_id: str) -> list[TwinStatusRow]:
        """List a job's twins (same persisted company_norm + title_norm).

        The job itself is excluded. Blank/NULL norms never cluster (the
        ``<> ''`` guards are NULL-safe: NULL comparisons are not true), so a
        blank-norm job has no twins — mirroring ``expand_twin_ids``.

        Args:
            job_id: Store-assigned identity of the detail job.

        Returns:
            Twin rows (platform, url, current status), ordered by job id.
        """
        pool = self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """SELECT twin.id, twin.platform, twin.url, job_status.status
                   FROM jobs
                   JOIN jobs twin
                     ON twin.company_norm = jobs.company_norm
                    AND twin.title_norm = jobs.title_norm
                    AND twin.id <> jobs.id
                   LEFT JOIN job_status ON job_status.job_id = twin.id
                   WHERE jobs.id = $1
                     AND jobs.company_norm <> ''
                     AND jobs.title_norm <> ''
                   ORDER BY twin.id""",
                int(job_id),
            )
        return [
            TwinStatusRow(
                job_id=str(r["id"]),
                platform=r["platform"],
                url=r["url"],
                status=r["status"],
            )
            for r in rows
        ]

    async def save_ml_gate_result(self, job_id: str, result: MLGateResult) -> None:
        """Persist ML gate decision and features.

        Args:
            job_id: Store-assigned identity.
            result: Gate decision with features.
        """
        domain_tags = json.dumps(result.domain_tags) if result.domain_tags else None
        tech_required = (
            json.dumps(result.tech_required) if result.tech_required else None
        )
        pool = self._get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """UPDATE jobs SET
                       ml_gate_score = $1, ml_gate_result = $2,
                       ml_gate_fail_reason = $3, ml_gate_at = now(),
                       ml_gate_version = $4, is_swe_role = $5,
                       seniority_level = $6, degree_required = $7,
                       clearance_required = $8, school_restricted = $9,
                       yoe_min = $10, domain_tags = $11,
                       tech_required = $12, role_type = $13
                   WHERE id = $14""",
                result.score,
                result.result,
                result.fail_reason,
                result.version,
                result.is_swe_role,
                result.seniority_level,
                result.degree_required,
                result.clearance_required,
                result.school_restricted,
                result.yoe_min,
                domain_tags,
                tech_required,
                result.role_type,
                int(job_id),
            )

    # ------------------------------------------------------------------
    # Pipeline runs
    # ------------------------------------------------------------------

    async def record_pipeline_run(self, run: PipelineRun) -> None:
        """Persist pipeline run counters.

        Args:
            run: Pipeline run.
        """
        pool = self._get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO pipeline_runs (
                       run_id, started_at, source, status, jobs_discovered,
                       jobs_inserted, jobs_updated, jobs_filtered,
                       jobs_ml_gated, stage_a_scored, stage_b_scored,
                       jobs_scored, total_llm_cost_usd, errors, finished_at
                   ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15)""",
                run.run_id,
                run.started_at,
                run.source,
                run.status,
                run.jobs_discovered,
                run.jobs_inserted,
                run.jobs_updated,
                run.jobs_filtered,
                run.jobs_ml_gated,
                run.stage_a_scored,
                run.stage_b_scored,
                run.jobs_scored,
                run.total_llm_cost_usd,
                run.errors,
                run.finished_at,
            )

    async def get_pipeline_run(self, run_id: str) -> PipelineRun | None:
        """Load a pipeline run by identity.

        Args:
            run_id: Run identity.

        Returns:
            Pipeline run if found, else None.
        """
        pool = self._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM pipeline_runs WHERE run_id = $1",
                run_id,
            )
        return _pipeline_run_from_record(row) if row is not None else None

    async def list_pipeline_runs(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[PipelineRun], int]:
        """List pipeline runs, newest first, with the all-time total.

        Args:
            limit: Maximum runs returned.
            offset: Runs to skip before the returned window.

        Returns:
            Tuple of (runs ordered by started_at DESC with run_id DESC as a
            deterministic tiebreak, total run count ignoring the window).
        """
        pool = self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """SELECT * FROM pipeline_runs
                   ORDER BY started_at DESC, run_id DESC
                   LIMIT $1 OFFSET $2""",
                limit,
                offset,
            )
            total = await self._count(conn, "SELECT COUNT(*) FROM pipeline_runs")
        return [_pipeline_run_from_record(r) for r in rows], total

    async def update_pipeline_run_status(
        self,
        run_id: str,
        status: str,
        finished_at: datetime | None = None,
    ) -> None:
        """Update a pipeline run's status and optional finish time.

        Args:
            run_id: Run identity.
            status: New status value (e.g. "succeeded", "failed").
            finished_at: Optional completion timestamp.
        """
        pool = self._get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """UPDATE pipeline_runs
                   SET status = $1, finished_at = $2
                   WHERE run_id = $3""",
                status,
                finished_at,
                run_id,
            )

    async def record_step_timing(self, timing: StepTiming) -> None:
        """Persist a single step timing record.

        Args:
            timing: Step timing to persist.
        """
        pool = self._get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO step_timings
                       (run_id, step_type, step_name, elapsed_ms, is_error)
                   VALUES ($1, $2, $3, $4, $5)""",
                timing.run_id,
                timing.step_type,
                timing.step_name,
                timing.elapsed_ms,
                timing.is_error,
            )

    async def record_step_timings(self, timings: list[StepTiming]) -> None:
        """Persist multiple step timing records in a single batch.

        Args:
            timings: Step timings to persist.
        """
        if not timings:
            return
        pool = self._get_pool()
        async with pool.acquire() as conn:
            await conn.executemany(
                """INSERT INTO step_timings
                       (run_id, step_type, step_name, elapsed_ms, is_error)
                   VALUES ($1, $2, $3, $4, $5)""",
                [
                    (t.run_id, t.step_type, t.step_name, t.elapsed_ms, t.is_error)
                    for t in timings
                ],
            )

    # ------------------------------------------------------------------
    # Performance queries
    # ------------------------------------------------------------------

    async def get_performance_overview(self, window_days: int) -> PerformanceOverview:
        """Aggregate performance metrics with period-over-period deltas.

        Uses two equal-sized windows (current and previous) to compute
        deltas. Falls back to None when no previous-window data exists.

        Args:
            window_days: Number of days in the current window.

        Returns:
            Overview with averages, totals, and deltas.
        """
        pool = self._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """WITH bounds AS (
                       SELECT now() - make_interval(days => $1) AS cur_start,
                              now() - make_interval(days => $1 * 2) AS prev_start
                   ),
                   cur AS (
                       SELECT
                           COALESCE(AVG(EXTRACT(EPOCH FROM (finished_at - started_at)) * 1000)
                               FILTER (WHERE source ILIKE '%%scan%%'), 0) AS avg_scan,
                           COALESCE(AVG(EXTRACT(EPOCH FROM (finished_at - started_at)) * 1000)
                               FILTER (WHERE source ILIKE '%%evaluat%%'), 0) AS avg_eval,
                           COALESCE(SUM(total_llm_cost_usd), 0) AS cost,
                           CASE WHEN COUNT(*) > 0
                               THEN COUNT(*) FILTER (WHERE errors > 0)::float / COUNT(*)
                               ELSE 0 END AS err_rate
                       FROM pipeline_runs, bounds
                       WHERE started_at >= bounds.cur_start
                         AND finished_at IS NOT NULL
                   ),
                   prev AS (
                       SELECT
                           COALESCE(AVG(EXTRACT(EPOCH FROM (finished_at - started_at)) * 1000)
                               FILTER (WHERE source ILIKE '%%scan%%'), 0) AS avg_scan,
                           COALESCE(AVG(EXTRACT(EPOCH FROM (finished_at - started_at)) * 1000)
                               FILTER (WHERE source ILIKE '%%evaluat%%'), 0) AS avg_eval,
                           COALESCE(SUM(total_llm_cost_usd), 0) AS cost,
                           CASE WHEN COUNT(*) > 0
                               THEN COUNT(*) FILTER (WHERE errors > 0)::float / COUNT(*)
                               ELSE 0 END AS err_rate,
                           COUNT(*) AS cnt
                       FROM pipeline_runs, bounds
                       WHERE started_at >= bounds.prev_start
                         AND started_at < bounds.cur_start
                         AND finished_at IS NOT NULL
                   )
                   SELECT cur.avg_scan, cur.avg_eval, cur.cost, cur.err_rate,
                          CASE WHEN prev.cnt > 0 AND prev.avg_scan > 0
                              THEN (cur.avg_scan - prev.avg_scan) / prev.avg_scan
                              ELSE NULL END AS scan_delta,
                          CASE WHEN prev.cnt > 0 AND prev.avg_eval > 0
                              THEN (cur.avg_eval - prev.avg_eval) / prev.avg_eval
                              ELSE NULL END AS eval_delta,
                          CASE WHEN prev.cnt > 0 AND prev.cost > 0
                              THEN (cur.cost - prev.cost) / prev.cost
                              ELSE NULL END AS cost_delta,
                          CASE WHEN prev.cnt > 0 AND prev.err_rate > 0
                              THEN (cur.err_rate - prev.err_rate) / prev.err_rate
                              ELSE NULL END AS err_delta
                   FROM cur, prev""",
                window_days,
            )
        assert row is not None
        return PerformanceOverview(
            avg_scan_duration_ms=float(row["avg_scan"]),
            avg_eval_duration_ms=float(row["avg_eval"]),
            total_llm_cost_usd=float(row["cost"]),
            error_rate=float(row["err_rate"]),
            scan_duration_delta=_opt_float(row["scan_delta"]),
            eval_duration_delta=_opt_float(row["eval_delta"]),
            cost_delta=_opt_float(row["cost_delta"]),
            error_rate_delta=_opt_float(row["err_delta"]),
        )

    async def get_step_timings(
        self, window_days: int, step_type: str | None = None
    ) -> list[StepTimingSeries]:
        """Fetch step timing rows within the window.

        Args:
            window_days: Look-back window in days.
            step_type: Optional filter on step_type.

        Returns:
            Step timings ordered by created_at ascending.
        """
        pool = self._get_pool()
        async with pool.acquire() as conn:
            if step_type is not None:
                rows = await conn.fetch(
                    """SELECT step_type, step_name, run_id,
                              elapsed_ms, is_error, created_at
                       FROM step_timings
                       WHERE created_at >= now() - make_interval(days => $1)
                         AND step_type = $2
                       ORDER BY created_at""",
                    window_days,
                    step_type,
                )
            else:
                rows = await conn.fetch(
                    """SELECT step_type, step_name, run_id,
                              elapsed_ms, is_error, created_at
                       FROM step_timings
                       WHERE created_at >= now() - make_interval(days => $1)
                       ORDER BY created_at""",
                    window_days,
                )
        return [
            StepTimingSeries(
                step_type=r["step_type"],
                step_name=r["step_name"],
                run_id=r["run_id"],
                elapsed_ms=float(r["elapsed_ms"]),
                is_error=r["is_error"],
                created_at=r["created_at"],
            )
            for r in rows
        ]

    async def get_llm_daily_stats(self, window_days: int) -> list[LLMDailyStats]:
        """Daily LLM latency percentiles and token averages.

        Args:
            window_days: Look-back window in days.

        Returns:
            Daily stats ordered by day ascending.
        """
        pool = self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """SELECT date(timestamp) AS day,
                          percentile_cont(0.5) WITHIN GROUP
                              (ORDER BY latency_ms) AS p50,
                          percentile_cont(0.95) WITHIN GROUP
                              (ORDER BY latency_ms) AS p95,
                          avg(input_tokens) AS avg_in,
                          avg(output_tokens) AS avg_out
                   FROM llm_usage
                   WHERE timestamp >= now() - make_interval(days => $1)
                   GROUP BY 1
                   ORDER BY 1""",
                window_days,
            )
        return [
            LLMDailyStats(
                day=r["day"].isoformat(),
                p50_latency_ms=float(r["p50"]),
                p95_latency_ms=float(r["p95"]),
                avg_input_tokens=float(r["avg_in"]),
                avg_output_tokens=float(r["avg_out"]),
            )
            for r in rows
        ]

    async def get_funnel_stats(self, window_days: int) -> list[FunnelStats]:
        """Evaluation funnel snapshots from pipeline runs.

        ``total_candidates`` is the sum of filtered, gated, and scored jobs
        for each run. ``after_filter`` removes the filtered count;
        ``after_gate`` further removes the gated count; ``scored`` is the
        final funnel count.

        Args:
            window_days: Look-back window in days.

        Returns:
            Funnel stats per run, newest first.
        """
        pool = self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """SELECT run_id,
                          jobs_filtered + jobs_ml_gated + jobs_scored
                              AS total_candidates,
                          jobs_ml_gated + jobs_scored AS after_filter,
                          jobs_scored AS after_gate,
                          jobs_scored AS scored
                   FROM pipeline_runs
                   WHERE started_at >= now() - make_interval(days => $1)
                   ORDER BY started_at DESC""",
                window_days,
            )
        return [
            FunnelStats(
                run_id=r["run_id"],
                total_candidates=r["total_candidates"],
                after_filter=r["after_filter"],
                after_gate=r["after_gate"],
                scored=r["scored"],
            )
            for r in rows
        ]

    async def insights_overview(self, *, window_days: int) -> InsightsOverview:
        """Aggregate the insights overview.

        Totals and the verdict/status distributions are all-time; only the
        daily series is windowed (UTC day buckets over
        ``[now - window_days, now]``, emitting only days having data).

        Args:
            window_days: Daily-series window in days (caller-validated).

        Returns:
            Insights overview aggregate.
        """
        pool = self._get_pool()
        async with pool.acquire() as conn:
            totals = await conn.fetchrow(_INSIGHTS_TOTALS_SQL)
            verdict_rows = await conn.fetch(_INSIGHTS_VERDICTS_SQL)
            status_rows = await conn.fetch(_INSIGHTS_STATUSES_SQL)
            discovered = await conn.fetch(
                _insights_day_sql("jobs", "discovered_at"), window_days
            )
            evaluated = await conn.fetch(
                _insights_day_sql("evaluations", "stage_a_at"), window_days
            )
            applied = await conn.fetch(
                _insights_day_sql("applied", "applied_at"), window_days
            )
        return InsightsOverview(
            window_days=window_days,
            total_jobs=int(totals["total_jobs"]),
            ml_gate_passed_jobs=int(totals["ml_gate_passed_jobs"]),
            evaluated_jobs=int(totals["evaluated_jobs"]),
            applied_jobs=int(totals["applied_jobs"]),
            verdict_distribution={r["bucket"]: int(r["n"]) for r in verdict_rows},
            status_distribution={r["bucket"]: int(r["n"]) for r in status_rows},
            daily=_merge_insights_days(discovered, evaluated, applied),
        )

    # ------------------------------------------------------------------
    # Status management
    # ------------------------------------------------------------------

    async def _transition_status_in_tx(
        self,
        conn: asyncpg.Connection,
        request: TransitionRequest,
    ) -> str:
        """Write status transition inside an existing transaction.

        The caller must hold a transaction via ``async with conn.transaction():``.

        Args:
            conn: Active asyncpg connection (transaction already open).
            request: Transition parameters unpacked inside this method.

        Returns:
            The new status string.

        Raises:
            ValueError: If the transition is invalid.
            KeyError: If the job has no status row.
        """
        job_id = request.job_id
        new_status = request.new_status
        reason = request.reason
        resume_variant = request.resume_variant
        force = request.force
        i_mean_it = request.i_mean_it
        followup_grace_days = request.followup_grace_days
        row = await conn.fetchrow(
            "SELECT status FROM job_status WHERE job_id = $1",
            int(job_id),
        )
        if row is None:
            raise KeyError(f"no status row for job_id={job_id}")
        old_status: str = row["status"]

        err = validate_transition(
            old_status, new_status, force=force, i_mean_it=i_mean_it
        )
        if err is not None:
            raise ValueError(err)

        # next_followup_at: set on transition to applied, cleared when leaving
        # the active-application states, untouched between interview substages.
        followup_val: datetime | None = None
        set_followup = new_status not in ACTIVE_APPLICATION_STATUSES
        if new_status == "applied":
            set_followup = True
            followup_val = datetime.now(UTC) + timedelta(days=followup_grace_days)

        reason_tag = reason
        if force and reason_tag is None:
            reason_tag = "FORCE"

        await conn.execute(
            """UPDATE job_status
               SET status = $1,
                   next_followup_at = CASE WHEN $2 THEN $3 ELSE next_followup_at END,
                   resume_variant = COALESCE($4, resume_variant),
                   last_status_change_at = now()
               WHERE job_id = $5""",
            new_status,
            set_followup,
            followup_val,
            resume_variant,
            int(job_id),
        )
        await conn.execute(
            """INSERT INTO job_status_history
                   (job_id, from_status, to_status, reason,
                    resume_variant_at_change)
               VALUES ($1, $2, $3, $4, $5)""",
            int(job_id),
            old_status,
            new_status,
            reason_tag,
            resume_variant,
        )
        return new_status

    async def transition_status(self, request: TransitionRequest) -> str:
        """Transition a job's status with validation and history.

        Args:
            request: Transition parameters.

        Returns:
            The new status string.

        Raises:
            ValueError: If the transition is invalid.
            KeyError: If the job has no status row.
        """
        pool = self._get_pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                return await self._transition_status_in_tx(conn, request)

    async def get_status(self, job_id: str) -> StatusInfo | None:
        """Get current status for a job.

        Args:
            job_id: Store-assigned identity.

        Returns:
            Status info if found, else None.
        """
        pool = self._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """SELECT s.job_id, s.status, s.next_followup_at,
                          s.resume_variant, s.notes, s.last_status_change_at,
                          j.company, j.title
                   FROM job_status s
                   JOIN jobs j ON j.id = s.job_id
                   WHERE s.job_id = $1""",
                int(job_id),
            )
        return _status_info_from_record(row) if row else None

    async def restore_from_archived(self, job_id: str) -> str:
        """Restore archived job to pre-archive status.

        Reads history and writes the restore transition all inside one
        transaction.

        Args:
            job_id: Store-assigned identity.

        Returns:
            Restored status string.

        Raises:
            ValueError: If job is not archived or no prior status exists.
        """
        pool = self._get_pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    "SELECT status FROM job_status WHERE job_id = $1",
                    int(job_id),
                )
                if row is None or row["status"] != "archived":
                    current = row["status"] if row else "unknown"
                    raise ValueError(f"job {job_id} is not archived (status={current})")

                hist = await conn.fetchrow(
                    # Order by the append-only id, not changed_at: the wall
                    # clock can invert two transitions written close together
                    # (see get_status_history), which would restore to the
                    # wrong target.
                    """SELECT to_status FROM job_status_history
                       WHERE job_id = $1 AND to_status != 'archived'
                       ORDER BY id DESC
                       LIMIT 1""",
                    int(job_id),
                )
                if hist is None:
                    raise ValueError(f"no non-archived history for job_id={job_id}")
                target_status: str = hist["to_status"]

                return await self._transition_status_in_tx(
                    conn,
                    TransitionRequest(
                        job_id=job_id,
                        new_status=target_status,
                        reason="restore_from_archived",
                        force=True,
                        i_mean_it=True,
                    ),
                )

    async def auto_decay(
        self,
        *,
        ghost_days: int = DEFAULT_GHOST_DAYS,
        archive_ignored_days: int = DEFAULT_ARCHIVE_IGNORED_DAYS,
    ) -> AutoDecayResult:
        """Sweep stale jobs to ghosted/archived.

        Args:
            ghost_days: Days before ghosting.
            archive_ignored_days: Days before archiving ignored.

        Returns:
            Counts of ghosted and archived jobs.
        """
        decay_list = sorted(DECAY_SOURCES)
        decay_placeholders = ", ".join(f"${i}" for i in range(1, len(decay_list) + 1))
        ghost_interval_param = f"${len(decay_list) + 1}"

        pool = self._get_pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                ghost_rows = await conn.fetch(
                    f"""SELECT job_id FROM job_status
                        WHERE status IN ({decay_placeholders})
                          AND last_status_change_at < now() - ({ghost_interval_param} || ' days')::interval""",
                    *decay_list,
                    str(ghost_days),
                )
                ghost_count = len(ghost_rows)
                for row in ghost_rows:
                    await self._transition_status_in_tx(
                        conn,
                        TransitionRequest(
                            job_id=str(row["job_id"]),
                            new_status="ghosted",
                            reason="auto_decay",
                            force=True,
                        ),
                    )

                archive_rows = await conn.fetch(
                    """SELECT job_id FROM job_status
                       WHERE status = 'ignored'
                         AND last_status_change_at < now() - ($1 || ' days')::interval""",
                    str(archive_ignored_days),
                )
                archive_count = len(archive_rows)
                for row in archive_rows:
                    await self._transition_status_in_tx(
                        conn,
                        TransitionRequest(
                            job_id=str(row["job_id"]),
                            new_status="archived",
                            reason="auto_decay",
                            force=True,
                        ),
                    )

        return AutoDecayResult(ghosted=ghost_count, archived=archive_count)

    # ------------------------------------------------------------------
    # StoreStatusMixin
    # ------------------------------------------------------------------

    async def list_statuses(
        self, filters: StatusFilter | None = None
    ) -> list[StatusInfo]:
        """Query jobs by status with optional filters.

        Args:
            filters: Filter parameters unpacked inside this method.

        Returns:
            Matching status info records.
        """
        _f = filters or StatusFilter()
        statuses = _f.statuses
        days = _f.days
        since = _f.since
        no_response_days = _f.no_response_days
        needs_followup = _f.needs_followup
        notes_contain = _f.notes_contain
        limit = _f.limit

        clauses: list[str] = []
        params: list[object] = []
        param_idx = 0

        if statuses:
            sorted_statuses = sorted(statuses)
            placeholders = ", ".join(
                f"${param_idx + i + 1}" for i in range(len(sorted_statuses))
            )
            clauses.append(f"s.status IN ({placeholders})")
            params.extend(sorted_statuses)
            param_idx += len(sorted_statuses)

        if days is not None:
            param_idx += 1
            clauses.append(
                f"s.last_status_change_at >= now() - (${param_idx} || ' days')::interval"
            )
            params.append(str(days))

        if since is not None:
            param_idx += 1
            clauses.append(f"s.last_status_change_at >= ${param_idx}")
            params.append(since)

        if no_response_days is not None:
            clauses.append("s.status IN ('applied', 'interviewing')")
            param_idx += 1
            clauses.append(
                f"s.last_status_change_at < now() - (${param_idx} || ' days')::interval"
            )
            params.append(str(no_response_days))

        if needs_followup:
            clauses.append("s.next_followup_at IS NOT NULL")
            clauses.append("s.next_followup_at::date <= CURRENT_DATE")

        if notes_contain:
            param_idx += 1
            clauses.append(f"s.notes ILIKE '%' || ${param_idx} || '%'")
            params.append(notes_contain)

        where = " AND ".join(clauses) if clauses else "1=1"
        sql = f"""SELECT s.job_id, s.status, s.next_followup_at,
                         s.resume_variant, s.notes, s.last_status_change_at,
                         j.company, j.title
                  FROM job_status s
                  JOIN jobs j ON j.id = s.job_id
                  WHERE {where}
                  ORDER BY s.last_status_change_at DESC"""

        if limit is not None:
            param_idx += 1
            sql += f" LIMIT ${param_idx}"
            params.append(limit)

        pool = self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(sql, *params)

        return [_status_info_from_record(r) for r in rows]

    async def append_note(self, *, job_id: str, text: str) -> bool:
        """Append timestamped note, reset ghost clock.

        Args:
            job_id: Store-assigned job identity.
            text: Note text to append.

        Returns:
            True if a job_status row was updated, False if none exists.
        """
        prefix = datetime.now(UTC).strftime("[%Y-%m-%d %H:%M] ")
        line = prefix + text + "\n"
        pool = self._get_pool()
        async with pool.acquire() as conn:
            result = await conn.execute(
                """UPDATE job_status
                   SET notes = COALESCE(notes, '') || $1,
                       last_status_change_at = now()
                   WHERE job_id = $2""",
                line,
                int(job_id),
            )
        # asyncpg returns "UPDATE N" where N is the row count
        return not result.endswith(" 0")

    async def set_followup(self, *, job_id: str, at: datetime) -> bool:
        """Set the next follow-up time for a job.

        Args:
            job_id: Store-assigned job identity.
            at: When the next follow-up is due.

        Returns:
            True if a job_status row was updated, False if none exists.
        """
        pool = self._get_pool()
        async with pool.acquire() as conn:
            result = await conn.execute(
                "UPDATE job_status SET next_followup_at = $1 WHERE job_id = $2",
                at,
                int(job_id),
            )
        # asyncpg returns "UPDATE N" where N is the row count
        return not result.endswith(" 0")

    async def workflow_attention(
        self,
        *,
        auto_ghost_days: int = DEFAULT_GHOST_DAYS,
        lookahead_days: int = 5,
    ) -> WorkflowAttention:
        """Three-bucket workflow attention report.

        Args:
            auto_ghost_days: Ghost threshold.
            lookahead_days: Early warning window.

        Returns:
            Follow-up, interview prep, and going-ghosted lists.
        """
        _cols = """s.job_id, j.title, j.company, j.url,
            s.status, s.last_status_change_at, s.next_followup_at, s.notes"""

        pool = self._get_pool()
        async with pool.acquire() as conn:
            # Bucket 1: follow_up_today
            follow_rows = await conn.fetch(
                f"""SELECT {_cols},
                       EXTRACT(DAY FROM now() - s.next_followup_at)::integer AS days_since
                   FROM job_status s JOIN jobs j ON j.id = s.job_id
                   WHERE s.status IN ('applied','interviewing')
                     AND s.next_followup_at IS NOT NULL
                     AND s.next_followup_at::date <= CURRENT_DATE
                   ORDER BY s.next_followup_at ASC""",
            )
            follow_up = [
                _attention_item_from_record(r, "follow-up due") for r in follow_rows
            ]

            # Bucket 2: interview_prep — source from interview_rounds
            # Jobs with upcoming scheduled rounds (bounded by lookahead window)
            upcoming_rows = await conn.fetch(
                f"""SELECT DISTINCT ON (s.job_id) {_cols},
                       ir.scheduled_at AS round_scheduled,
                       EXTRACT(DAY FROM now() - s.last_status_change_at)::integer
                           AS days_since
                   FROM job_status s
                   JOIN jobs j ON j.id = s.job_id
                   JOIN interview_rounds ir ON ir.job_id = s.job_id
                   WHERE s.status = 'interviewing'
                     AND ir.completed_at IS NULL
                     AND ir.scheduled_at IS NOT NULL
                     AND ir.scheduled_at <= now() + ($1 || ' days')::interval
                   ORDER BY s.job_id, ir.scheduled_at ASC""",
                str(lookahead_days),
            )
            interview = [
                _attention_item_from_record(r, "interview prep") for r in upcoming_rows
            ]
            # Also include interviewing jobs with no upcoming round
            unscheduled_rows = await conn.fetch(
                f"""SELECT {_cols},
                       EXTRACT(DAY FROM now() - s.last_status_change_at)::integer
                           AS days_since
                   FROM job_status s
                   JOIN jobs j ON j.id = s.job_id
                   WHERE s.status = 'interviewing'
                     AND NOT EXISTS (
                         SELECT 1 FROM interview_rounds ir
                         WHERE ir.job_id = s.job_id
                           AND ir.completed_at IS NULL
                           AND ir.scheduled_at IS NOT NULL
                     )
                   ORDER BY s.last_status_change_at DESC""",
            )
            interview.extend(
                _attention_item_from_record(r, "interviewing, unscheduled")
                for r in unscheduled_rows
            )

            # Bucket 3: going_ghosted — every decay-eligible status, not just
            # applied/interviewing, so substages warn before they are ghosted.
            warn_days = auto_ghost_days - lookahead_days
            ghosting_rows = await conn.fetch(
                f"""SELECT {_cols},
                       EXTRACT(DAY FROM now() - s.last_status_change_at)::integer AS days_since
                   FROM job_status s JOIN jobs j ON j.id = s.job_id
                   WHERE s.status = ANY($2)
                     AND s.last_status_change_at < now() - ($1 || ' days')::interval
                   ORDER BY s.last_status_change_at ASC""",
                str(warn_days),
                sorted(DECAY_SOURCES),
            )
            ghosting = [
                _attention_item_from_record(r, "going silent") for r in ghosting_rows
            ]

        return WorkflowAttention(
            follow_up_today=follow_up,
            interview_prep=interview,
            going_ghosted=ghosting,
        )

    async def compute_reapply_notice(
        self,
        *,
        job_id: str,
        lookback_days: int = 60,
    ) -> str | None:
        """Detect same-company active application.

        Args:
            job_id: Job to check.
            lookback_days: How far back to look.

        Returns:
            Notice string if detected, else None.
        """
        pool = self._get_pool()
        async with pool.acquire() as conn:
            job_row = await conn.fetchrow(
                "SELECT company, company_norm FROM jobs WHERE id = $1",
                int(job_id),
            )
            if job_row is None:
                return None

            company_norm = job_row["company_norm"] or normalize_company(
                job_row["company"]
            )
            if not company_norm:
                return None

            match = await conn.fetchrow(
                """SELECT j.id, j.title, s.status
                   FROM jobs j
                   JOIN job_status s ON s.job_id = j.id
                   WHERE (j.company_norm = $1 OR $1 = '')
                     AND j.id != $2
                     AND s.status = ANY($4)
                     AND s.last_status_change_at >= now() - ($3 || ' days')::interval
                   LIMIT 1""",
                company_norm,
                int(job_id),
                str(lookback_days),
                sorted(ACTIVE_APPLICATION_STATUSES),
            )

        if match is None:
            return None
        return (
            f"Active application at same company: "
            f"'{match['title']}' (job {match['id']}, status={match['status']})"
        )

    async def get_status_history(self, job_id: str) -> list[str]:
        """Return to_status values from job_status_history, newest-first.

        Ordered by the append-only ``id`` (BIGSERIAL), not ``changed_at``:
        ``changed_at`` defaults to ``now()`` (the wall clock at transaction
        start), which is not monotonic under NTP steps or load, so two
        transitions written milliseconds apart can invert. ``id`` is the
        gap-free insertion order and stays correct for legacy-imported rows
        too (their ids preserve original chronology, and the sequence is
        bumped past them on import).

        Args:
            job_id: Store-assigned identity.

        Returns:
            List of status strings in reverse chronological order.
        """
        pool = self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT to_status FROM job_status_history"
                " WHERE job_id = $1 ORDER BY id DESC",
                int(job_id),
            )
        return [r["to_status"] for r in rows]

    async def expand_twin_ids(self, job_ids: list[int]) -> dict[int, list[int]]:
        """Expand each job_id to its twin cluster (same company_norm + title_norm).

        A row with blank company_norm or title_norm expands to itself only.

        Args:
            job_ids: Store-assigned job identities.

        Returns:
            Mapping of job_id to list of twin cluster member ids.
        """
        if not job_ids:
            return {}
        pool = self._get_pool()
        result: dict[int, list[int]] = {}
        async with pool.acquire() as conn:
            for jid in job_ids:
                row = await conn.fetchrow(
                    "SELECT company_norm, title_norm FROM jobs WHERE id = $1",
                    jid,
                )
                if row is None:
                    result[jid] = [jid]
                    continue
                cn = row["company_norm"] or ""
                tn = row["title_norm"] or ""
                if not cn or not tn:
                    result[jid] = [jid]
                    continue
                twins = await conn.fetch(
                    "SELECT id FROM jobs WHERE company_norm = $1 AND title_norm = $2",
                    cn,
                    tn,
                )
                result[jid] = [r["id"] for r in twins]
        return result

    async def transition_status_bulk(
        self, request: BulkTransitionRequest
    ) -> BulkResult:
        """Transition each item plus its twin cluster. Atomic per cluster.

        The selected job gets reason_selected, twins get reason_cascade.
        Each cluster is transitioned in its own transaction; a failing
        cluster is recorded and does not block others.

        Time complexity: O(N * C) where N = len(items) and C is the average
        twin-cluster size (typically 1-3).

        Args:
            request: Bulk transition parameters unpacked inside this method.

        Returns:
            Summary of succeeded, failed, and skipped transitions.
        """
        items = request.items
        reason_selected = request.reason_selected
        reason_cascade = request.reason_cascade
        force = request.force
        i_mean_it = request.i_mean_it

        result = BulkResult()
        twin_map = await self.expand_twin_ids([int(jid) for jid, _ in items])
        pool = self._get_pool()
        processed_ids: set[int] = set()

        for job_id, new_status in items:
            if int(job_id) in processed_ids:
                continue
            cluster = twin_map.get(int(job_id), [int(job_id)])
            try:
                cluster_ok = 0
                cluster_skipped = 0
                cluster_cascaded = 0
                async with pool.acquire() as conn:
                    async with conn.transaction():
                        await self._transition_status_in_tx(
                            conn,
                            TransitionRequest(
                                job_id=job_id,
                                new_status=new_status,
                                reason=reason_selected,
                                force=force,
                                i_mean_it=i_mean_it,
                            ),
                        )
                        cluster_ok += 1
                        for twin_id in cluster:
                            if twin_id == int(job_id):
                                continue
                            status_row = await conn.fetchrow(
                                "SELECT status FROM job_status WHERE job_id = $1",
                                twin_id,
                            )
                            if status_row is None:
                                continue
                            if is_terminal(status_row["status"]):
                                cluster_skipped += 1
                                continue
                            await self._transition_status_in_tx(
                                conn,
                                TransitionRequest(
                                    job_id=str(twin_id),
                                    new_status=new_status,
                                    reason=reason_cascade,
                                    force=force,
                                    i_mean_it=i_mean_it,
                                ),
                            )
                            cluster_ok += 1
                            cluster_cascaded += 1
                result.succeeded += cluster_ok
                result.skipped += cluster_skipped
                result.cascaded += cluster_cascaded
                processed_ids.update(cluster)
            except Exception as exc:
                result.failed.append((job_id, str(exc)))

        return result

    # ------------------------------------------------------------------
    # StoreApplicationMixin
    # ------------------------------------------------------------------

    async def record_application(self, record: ApplicationRecord) -> bool:
        """Record application with atomic status transition.

        Inserts into the ``applied`` table and, if the row is new,
        transitions the job status to ``'applied'`` inside the same
        transaction.  Guards against terminal statuses.

        Args:
            record: Application audit record.

        Returns:
            True if new, False if already applied.

        Raises:
            ValueError: If the job is in a terminal status.
        """
        job_id_int = int(record.job_id)
        pool = self._get_pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                # Insert application first (ON CONFLICT DO NOTHING for idempotence).
                # A duplicate is a no-op regardless of current status, so the
                # terminal guard must come after this for idempotence.
                result = await conn.execute(
                    """INSERT INTO applied
                           (job_id, applied_at, notes,
                            master_resume_hash, tailored_resume_hash,
                            cover_letter, application_method,
                            verdict_snapshot, fit_snapshot, hooks_snapshot)
                       VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
                       ON CONFLICT (job_id) DO NOTHING""",
                    job_id_int,
                    record.applied_at,
                    record.notes,
                    record.master_resume_hash,
                    record.tailored_resume_hash,
                    record.cover_letter,
                    record.application_method,
                    record.verdict_snapshot,
                    record.fit_snapshot,
                    record.hooks_snapshot,
                )

                # asyncpg returns status string like "INSERT 0 1" or "INSERT 0 0"
                if result == "INSERT 0 0":
                    return False

                # New application: guard against applying from a terminal status.
                status_row = await conn.fetchrow(
                    "SELECT status FROM job_status WHERE job_id = $1",
                    job_id_int,
                )
                if status_row is not None and is_terminal(status_row["status"]):
                    raise ValueError(
                        f"cannot apply from terminal status '{status_row['status']}'"
                    )

                # Transition status to applied
                now = datetime.now(UTC)
                followup_val = now + timedelta(days=DEFAULT_FOLLOWUP_GRACE_DAYS)
                from_status: str | None = status_row["status"] if status_row else None

                await conn.execute(
                    """UPDATE job_status
                       SET status = 'applied',
                           last_status_change_at = $1,
                           next_followup_at = COALESCE($2, next_followup_at),
                           resume_variant = COALESCE($3, resume_variant)
                       WHERE job_id = $4""",
                    now,
                    followup_val,
                    None,
                    job_id_int,
                )

                await conn.execute(
                    """INSERT INTO job_status_history
                           (job_id, from_status, to_status, changed_at,
                            reason, resume_variant_at_change)
                       VALUES ($1, $2, 'applied', $3, 'record_application', $4)""",
                    job_id_int,
                    from_status,
                    now,
                    None,
                )

        return True

    # -- in-tx helpers ---------------------------------------------------

    async def _save_resume_snapshot_in_tx(
        self,
        conn: asyncpg.Connection,
        snapshot: ResumeSnapshot,
    ) -> None:
        """Write a resume snapshot inside an existing transaction.

        Args:
            conn: Connection with an active transaction.
            snapshot: Resume snapshot to persist.
        """
        await conn.execute(
            """INSERT INTO resume_snapshots
                   (resume_hash, captured_at, source, content, notes)
               VALUES ($1, $2, $3, $4, $5)
               ON CONFLICT (resume_hash) DO NOTHING""",
            snapshot.resume_hash,
            snapshot.captured_at,
            snapshot.source,
            snapshot.content,
            snapshot.notes,
        )

    async def _register_resume_variant_in_tx(
        self,
        conn: asyncpg.Connection,
        name: str,
    ) -> None:
        """Register a resume variant inside an existing transaction.

        Args:
            conn: Connection with an active transaction.
            name: Variant name to register.
        """
        await conn.execute(
            """INSERT INTO resume_variants (name, description)
               VALUES ($1, NULL)
               ON CONFLICT (name) DO NOTHING""",
            name,
        )

    async def record_application_with_snapshots(
        self,
        record: ApplicationRecord,
        *,
        snapshots: list[ResumeSnapshot] | None = None,
        resume_variant: str | None = None,
    ) -> bool:
        """Record application with resume snapshots in one atomic transaction.

        Order: variant registration -> snapshot upserts -> applied INSERT ->
        idempotency check -> terminal-status guard -> status transition.
        On failure after snapshot upsert the entire transaction rolls back.

        Args:
            record: Application audit record.
            snapshots: Optional resume snapshots to persist atomically.
            resume_variant: Optional variant name to set on the job status;
                auto-registered if not already known.

        Returns:
            True if new, False if already applied.

        Raises:
            ValueError: If the job is in a terminal status.
        """
        job_id_int = int(record.job_id)
        pool = self._get_pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                # 1. Auto-register variant if provided.
                if resume_variant is not None:
                    await self._register_resume_variant_in_tx(conn, resume_variant)

                # 2. Upsert resume snapshots.
                for snap in snapshots or []:
                    await self._save_resume_snapshot_in_tx(conn, snap)

                # 3. Insert application (ON CONFLICT DO NOTHING).
                result = await conn.execute(
                    """INSERT INTO applied
                           (job_id, applied_at, notes,
                            master_resume_hash, tailored_resume_hash,
                            cover_letter, application_method,
                            verdict_snapshot, fit_snapshot, hooks_snapshot)
                       VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
                       ON CONFLICT (job_id) DO NOTHING""",
                    job_id_int,
                    record.applied_at,
                    record.notes,
                    record.master_resume_hash,
                    record.tailored_resume_hash,
                    record.cover_letter,
                    record.application_method,
                    record.verdict_snapshot,
                    record.fit_snapshot,
                    record.hooks_snapshot,
                )

                # 4. Idempotency: duplicate is a no-op.
                if result == "INSERT 0 0":
                    return False

                # 5. Terminal-status guard.
                status_row = await conn.fetchrow(
                    "SELECT status FROM job_status WHERE job_id = $1",
                    job_id_int,
                )
                if status_row is not None and is_terminal(status_row["status"]):
                    raise ValueError(
                        f"cannot apply from terminal status '{status_row['status']}'"
                    )

                # 6. Transition status to applied — but only if the job is
                # not already in an active-application status (applied,
                # interviewing).  Regressing from interviewing to applied
                # would lose progress.
                from_status: str | None = status_row["status"] if status_row else None
                is_already_active = (
                    from_status is not None
                    and from_status in ACTIVE_APPLICATION_STATUSES
                )
                now = datetime.now(UTC)

                if is_already_active:
                    # Still record the audit trail without changing status.
                    await conn.execute(
                        """INSERT INTO job_status_history
                               (job_id, from_status, to_status, changed_at,
                                reason, resume_variant_at_change)
                           VALUES ($1, $2, $3, $4,
                                   'record_application_noop', $5)""",
                        job_id_int,
                        from_status,
                        from_status,
                        now,
                        resume_variant,
                    )
                    # Update resume_variant even when status stays unchanged.
                    if resume_variant:
                        await conn.execute(
                            "UPDATE job_status SET resume_variant = $1"
                            " WHERE job_id = $2",
                            resume_variant,
                            job_id_int,
                        )
                else:
                    followup_val = now + timedelta(
                        days=DEFAULT_FOLLOWUP_GRACE_DAYS,
                    )
                    await conn.execute(
                        """UPDATE job_status
                           SET status = 'applied',
                               last_status_change_at = $1,
                               next_followup_at = COALESCE($2, next_followup_at),
                               resume_variant = COALESCE($3, resume_variant)
                           WHERE job_id = $4""",
                        now,
                        followup_val,
                        resume_variant,
                        job_id_int,
                    )

                    await conn.execute(
                        """INSERT INTO job_status_history
                               (job_id, from_status, to_status, changed_at,
                                reason, resume_variant_at_change)
                           VALUES ($1, $2, 'applied', $3,
                                   'record_application', $4)""",
                        job_id_int,
                        from_status,
                        now,
                        resume_variant,
                    )

        return True

    async def get_application(self, job_id: str) -> ApplicationRecord | None:
        """Load a single application record by job_id.

        Args:
            job_id: Store-assigned job identity.

        Returns:
            Application record if found, else None.
        """
        pool = self._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """SELECT job_id, applied_at,
                          master_resume_hash, tailored_resume_hash,
                          cover_letter, application_method,
                          verdict_snapshot, fit_snapshot,
                          hooks_snapshot, notes
                   FROM applied WHERE job_id = $1""",
                int(job_id),
            )
        if row is None:
            return None
        return ApplicationRecord(
            job_id=str(row["job_id"]),
            applied_at=row["applied_at"],
            master_resume_hash=row["master_resume_hash"],
            tailored_resume_hash=row["tailored_resume_hash"],
            cover_letter=row["cover_letter"],
            application_method=row["application_method"],
            verdict_snapshot=row["verdict_snapshot"],
            fit_snapshot=row["fit_snapshot"],
            hooks_snapshot=row["hooks_snapshot"],
            notes=row["notes"],
        )

    async def list_applications(
        self,
        *,
        limit: int = 100,
        resume_hash_prefix: str | None = None,
    ) -> list[ApplicationRecord]:
        """List application records by recency.

        Args:
            limit: Max records.
            resume_hash_prefix: Optional literal hash prefix; keeps only
                records whose master OR tailored resume hash starts with it.

        Returns:
            Application records.
        """
        pattern = (
            None
            if resume_hash_prefix is None
            else _escape_like_prefix(resume_hash_prefix) + "%"
        )
        pool = self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                r"""SELECT a.job_id, a.applied_at,
                          a.master_resume_hash, a.tailored_resume_hash,
                          a.cover_letter, a.application_method,
                          a.verdict_snapshot, a.fit_snapshot,
                          a.hooks_snapshot, a.notes
                   FROM applied a
                   JOIN jobs j ON a.job_id = j.id
                   WHERE $2::text IS NULL
                      OR a.master_resume_hash LIKE $2 ESCAPE '\'
                      OR a.tailored_resume_hash LIKE $2 ESCAPE '\'
                   ORDER BY a.applied_at DESC
                   LIMIT $1""",
                limit,
                pattern,
            )

        return [
            ApplicationRecord(
                job_id=str(r["job_id"]),
                applied_at=r["applied_at"],
                master_resume_hash=r["master_resume_hash"],
                tailored_resume_hash=r["tailored_resume_hash"],
                cover_letter=r["cover_letter"],
                application_method=r["application_method"],
                verdict_snapshot=r["verdict_snapshot"],
                fit_snapshot=r["fit_snapshot"],
                hooks_snapshot=r["hooks_snapshot"],
                notes=r["notes"],
            )
            for r in rows
        ]

    async def application_stats(
        self,
        *,
        since_days_ago: int = 30,
        by_resume: bool = False,
    ) -> ApplicationStats:
        """Aggregate application statistics.

        Args:
            since_days_ago: Time window.
            by_resume: Include per-variant breakdown.

        Returns:
            Application statistics.
        """
        cutoff = datetime.now(UTC) - timedelta(days=since_days_ago)
        _empty = ApplicationStats(
            applied_count=0,
            response_count=0,
            interview_count=0,
            offer_count=0,
            rejection_count=0,
            median_days_to_response=None,
            by_resume=None,
        )

        pool = self._get_pool()
        async with pool.acquire() as conn:
            # 1. Applied jobs in window
            applied_rows = await conn.fetch(
                """SELECT DISTINCT job_id
                   FROM job_status_history
                   WHERE to_status = 'applied'
                     AND changed_at >= $1""",
                cutoff,
            )
            applied_job_ids = {r["job_id"] for r in applied_rows}

            if not applied_job_ids:
                return _empty

            id_list = sorted(applied_job_ids)
            id_ph = ", ".join(f"${i + 1}" for i in range(len(id_list)))
            resp_sorted = sorted(RESPONSE_STATUSES)
            resp_offset = len(id_list)
            resp_ph = ", ".join(
                f"${resp_offset + i + 1}" for i in range(len(resp_sorted))
            )
            # 2. Response statuses reached
            response_rows = await conn.fetch(
                f"""SELECT DISTINCT job_id, to_status
                    FROM job_status_history
                    WHERE job_id IN ({id_ph})
                      AND to_status IN ({resp_ph})""",
                *id_list,
                *resp_sorted,
            )

            job_resp: dict[int, set[str]] = {}
            for r in response_rows:
                job_resp.setdefault(r["job_id"], set()).add(r["to_status"])

            resp_ct, int_ct, off_ct, rej_ct = _count_outcomes(job_resp)

            # 3. Median days to first response (single CTE query)
            resp_job_ids = sorted(job_resp.keys())
            delta_rows = await conn.fetch(
                """WITH applied_ts AS (
                       SELECT job_id, MIN(changed_at) AS applied_at
                       FROM job_status_history
                       WHERE job_id = ANY($1) AND to_status = 'applied'
                       GROUP BY job_id
                   ), resp_ts AS (
                       SELECT job_id, MIN(changed_at) AS resp_at
                       FROM job_status_history
                       WHERE job_id = ANY($1) AND to_status = ANY($2)
                       GROUP BY job_id
                   )
                   SELECT EXTRACT(DAY FROM r.resp_at - a.applied_at)::integer AS days
                   FROM applied_ts a
                   JOIN resp_ts r ON r.job_id = a.job_id""",
                resp_job_ids,
                resp_sorted,
            )
            deltas = [row["days"] for row in delta_rows]
            median_days = _median(deltas)

            # 4. Per-variant breakdown (optional)
            by_resume_dict: dict[str, ResumeVariantStats] | None = None
            if by_resume:
                vrows = await conn.fetch(
                    f"""SELECT job_id, resume_variant_at_change
                        FROM job_status_history
                        WHERE job_id IN ({id_ph})
                          AND to_status = 'applied'
                          AND changed_at >= ${len(id_list) + 1}""",
                    *id_list,
                    cutoff,
                )
                variant_rows = [
                    (r["job_id"], r["resume_variant_at_change"]) for r in vrows
                ]

                # Build per-variant stats
                job_variant: dict[int, str] = {}
                for jid_v, raw_name in variant_rows:
                    job_variant[jid_v] = raw_name if raw_name is not None else "unknown"

                variant_jobs: dict[str, set[int]] = {}
                for jid_v, vname in job_variant.items():
                    variant_jobs.setdefault(vname, set()).add(jid_v)

                by_resume_dict = {}
                for vname, jids in sorted(variant_jobs.items()):
                    v_resp = {j: job_resp[j] for j in jids if j in job_resp}
                    by_resume_dict[vname] = ResumeVariantStats(
                        sent=len(jids),
                        responses=len(v_resp),
                        interviews=sum(
                            1 for s in v_resp.values() if s & _INTERVIEW_STATUSES
                        ),
                        offers=sum(1 for s in v_resp.values() if "offer" in s),
                        rejections=sum(1 for s in v_resp.values() if "rejected" in s),
                    )

        return ApplicationStats(
            applied_count=len(applied_job_ids),
            response_count=resp_ct,
            interview_count=int_ct,
            offer_count=off_ct,
            rejection_count=rej_ct,
            median_days_to_response=median_days,
            by_resume=by_resume_dict,
        )

    async def save_resume_snapshot(self, snapshot: ResumeSnapshot) -> None:
        """Content-addressed resume insert (no-op if exists).

        Args:
            snapshot: Resume snapshot to persist.
        """
        pool = self._get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO resume_snapshots
                       (resume_hash, captured_at, source, content, notes)
                   VALUES ($1, $2, $3, $4, $5)
                   ON CONFLICT (resume_hash) DO NOTHING""",
                snapshot.resume_hash,
                snapshot.captured_at,
                snapshot.source,
                snapshot.content,
                snapshot.notes,
            )

    async def get_resume_snapshot(
        self,
        resume_hash: str,
    ) -> ResumeSnapshot | None:
        """Load resume snapshot by hash.

        Args:
            resume_hash: Content-addressed hash.

        Returns:
            Snapshot if found, else None.
        """
        pool = self._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """SELECT resume_hash, captured_at, source, content, notes
                   FROM resume_snapshots
                   WHERE resume_hash = $1""",
                resume_hash,
            )
        if row is None:
            return None
        return ResumeSnapshot(
            resume_hash=row["resume_hash"],
            captured_at=row["captured_at"],
            source=row["source"],
            content=row["content"],
            notes=row["notes"],
        )

    async def get_resume_snapshot_by_prefix(self, prefix: str) -> ResumeSnapshot:
        """Resolve a resume snapshot by a unique hash prefix.

        Args:
            prefix: Literal hash prefix (LIKE wildcards are escaped).

        Returns:
            The single matching snapshot.

        Raises:
            SnapshotNotFoundError: If no snapshot matches the prefix.
            SnapshotAmbiguousError: If two or more snapshots match.
        """
        pattern = _escape_like_prefix(prefix) + "%"
        pool = self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                r"""SELECT resume_hash, captured_at, source, content, notes
                   FROM resume_snapshots
                   WHERE resume_hash LIKE $1 ESCAPE '\'
                   LIMIT 2""",
                pattern,
            )
        if not rows:
            raise SnapshotNotFoundError(f"no resume snapshot matches prefix {prefix!r}")
        if len(rows) > 1:
            raise SnapshotAmbiguousError(
                f"resume hash prefix {prefix!r} matches multiple snapshots"
            )
        row = rows[0]
        return ResumeSnapshot(
            resume_hash=row["resume_hash"],
            captured_at=row["captured_at"],
            source=row["source"],
            content=row["content"],
            notes=row["notes"],
        )

    async def list_resume_snapshots(
        self,
        source: str | None = None,
    ) -> list[ResumeSnapshotSummary]:
        """List every resume snapshot with its applied-row usage count.

        Usage count is the number of ``applied`` rows referencing the hash
        as master OR tailored resume (LEFT JOIN, so orphans count 0 and
        still appear).

        Args:
            source: Optional filter on the stored source column.

        Returns:
            Snapshot summaries (without content), newest first.
        """
        pool = self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """SELECT s.resume_hash, s.captured_at, s.source,
                          COUNT(a.job_id)::int AS usage_count
                   FROM resume_snapshots s
                   LEFT JOIN applied a
                     ON a.master_resume_hash = s.resume_hash
                     OR a.tailored_resume_hash = s.resume_hash
                   WHERE $1::text IS NULL OR s.source = $1
                   GROUP BY s.resume_hash, s.captured_at, s.source
                   ORDER BY s.captured_at DESC, s.resume_hash""",
                source,
            )
        return [
            ResumeSnapshotSummary(
                resume_hash=r["resume_hash"],
                captured_at=r["captured_at"],
                source=r["source"],
                usage_count=r["usage_count"],
            )
            for r in rows
        ]

    async def register_resume_variant(
        self,
        *,
        name: str,
        description: str | None = None,
    ) -> bool:
        """Register a named resume variant.

        Args:
            name: Variant name.
            description: Optional description.

        Returns:
            True if new, False if existed.
        """
        pool = self._get_pool()
        async with pool.acquire() as conn:
            result = await conn.execute(
                """INSERT INTO resume_variants (name, description)
                   VALUES ($1, $2)
                   ON CONFLICT (name) DO NOTHING""",
                name,
                description,
            )
        # asyncpg returns "INSERT 0 1" for inserted, "INSERT 0 0" for conflict
        return bool(result == "INSERT 0 1")

    # ------------------------------------------------------------------
    # StoreInterviewMixin
    # ------------------------------------------------------------------

    async def add_interview_round(
        self,
        *,
        job_id: str,
        label: str,
        scheduled_at: datetime | None = None,
    ) -> InterviewRound:
        """Append a new interview round to a job.

        Assigns round_index atomically via INSERT ... SELECT MAX+1.
        Retries once on the rare unique-constraint race.

        Args:
            job_id: Store-assigned job identity.
            label: Human-readable round label.
            scheduled_at: Optional scheduled interview time.

        Returns:
            The newly created interview round.
        """
        jid = int(job_id)
        pool = self._get_pool()
        sql = (
            "INSERT INTO interview_rounds"
            " (job_id, round_index, label, scheduled_at)"
            " SELECT $1, COALESCE(MAX(round_index), 0) + 1, $2, $3"
            " FROM interview_rounds WHERE job_id = $1"
            " RETURNING *"
        )
        async with pool.acquire() as conn:
            async with conn.transaction():
                try:
                    async with conn.transaction():  # savepoint
                        row = await conn.fetchrow(sql, jid, label, scheduled_at)
                except asyncpg.UniqueViolationError:
                    row = await conn.fetchrow(sql, jid, label, scheduled_at)
                await conn.execute(
                    "UPDATE job_status"
                    " SET last_status_change_at = now()"
                    " WHERE job_id = $1",
                    jid,
                )
        assert row is not None  # INSERT ... RETURNING always yields a row
        return _row_to_interview_round(row)

    async def list_interview_rounds(
        self,
        job_id: str,
    ) -> list[InterviewRound]:
        """List all interview rounds for a job, ordered by round_index.

        Args:
            job_id: Store-assigned job identity.

        Returns:
            Interview rounds in ascending round_index order.
        """
        pool = self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM interview_rounds WHERE job_id = $1 ORDER BY round_index",
                int(job_id),
            )
        return [_row_to_interview_round(r) for r in rows]

    async def complete_interview_round(
        self,
        *,
        job_id: str,
        round_index: int | None = None,
        notes: str | None = None,
    ) -> InterviewRound:
        """Mark an interview round as completed.

        If round_index is None, completes the latest open round.

        Args:
            job_id: Store-assigned job identity.
            round_index: Specific round to complete, or None for latest open.
            notes: Optional notes to attach.

        Returns:
            The completed interview round.

        Raises:
            ValueError: If no open interview round exists.
        """
        jid = int(job_id)
        pool = self._get_pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                if round_index is not None:
                    target = await conn.fetchrow(
                        "SELECT * FROM interview_rounds"
                        " WHERE job_id = $1 AND round_index = $2"
                        " AND completed_at IS NULL",
                        jid,
                        round_index,
                    )
                else:
                    target = await conn.fetchrow(
                        "SELECT * FROM interview_rounds"
                        " WHERE job_id = $1 AND completed_at IS NULL"
                        " ORDER BY round_index DESC LIMIT 1",
                        jid,
                    )
                if target is None:
                    raise ValueError(f"no open interview round for job_id={job_id}")
                row = await conn.fetchrow(
                    "UPDATE interview_rounds"
                    " SET completed_at = now(),"
                    " notes = COALESCE($1, notes)"
                    " WHERE id = $2"
                    " RETURNING *",
                    notes,
                    target["id"],
                )
                await conn.execute(
                    "UPDATE job_status"
                    " SET last_status_change_at = now()"
                    " WHERE job_id = $1",
                    jid,
                )
        assert row is not None
        return _row_to_interview_round(row)

    async def list_upcoming_interviews(
        self,
        *,
        within_days: int = 7,
    ) -> list[InterviewRound]:
        """List scheduled but not-yet-completed interviews within a time window.

        Only returns rounds whose parent job is currently in 'interviewing'
        status, so rounds for rejected/ghosted/offered jobs are excluded.

        Args:
            within_days: Number of days ahead to look.

        Returns:
            Upcoming interview rounds ordered by scheduled_at.
        """
        pool = self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT ir.* FROM interview_rounds ir"
                " JOIN job_status js ON js.job_id = ir.job_id"
                " WHERE ir.scheduled_at IS NOT NULL"
                " AND ir.scheduled_at <= now() + interval '1 day' * $1"
                " AND ir.completed_at IS NULL"
                " AND js.status = 'interviewing'"
                " ORDER BY ir.scheduled_at",
                within_days,
            )
        return [_row_to_interview_round(r) for r in rows]

    # ------------------------------------------------------------------
    # StoreOpsMixin
    # ------------------------------------------------------------------

    async def upsert_company(self, company: CompanyRecord) -> None:
        """Insert or update a company record.

        Args:
            company: Company record.
        """
        pool = self._get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO companies (
                       slug, ats_vendor, ats_override, last_verified_at,
                       last_probe_attempt_at, job_count_last_scan, notes,
                       consecutive_discover_failures
                   ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
                   ON CONFLICT (slug) DO UPDATE SET
                       ats_vendor = COALESCE(EXCLUDED.ats_vendor, companies.ats_vendor),
                       ats_override = EXCLUDED.ats_override,
                       last_verified_at = COALESCE(EXCLUDED.last_verified_at,
                           companies.last_verified_at),
                       last_probe_attempt_at = COALESCE(EXCLUDED.last_probe_attempt_at,
                           companies.last_probe_attempt_at),
                       job_count_last_scan = EXCLUDED.job_count_last_scan,
                       notes = COALESCE(EXCLUDED.notes, companies.notes),
                       consecutive_discover_failures = EXCLUDED.consecutive_discover_failures""",
                company.slug,
                company.ats_vendor,
                int(company.ats_override),
                company.last_verified_at,
                company.last_probe_attempt_at,
                company.job_count_last_scan,
                company.notes,
                company.consecutive_discover_failures,
            )

    async def get_company(self, slug: str) -> CompanyRecord | None:
        """Load a company by slug.

        Args:
            slug: Company slug.

        Returns:
            Company record if found, else None.
        """
        pool = self._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM companies WHERE slug = $1",
                slug,
            )
        return _company_from_record(row) if row is not None else None

    async def list_companies(
        self,
        *,
        vendor: str | None = None,
        include_removed: bool = False,
    ) -> list[CompanyRecord]:
        """List companies with optional filters.

        Args:
            vendor: Filter by ATS vendor.
            include_removed: Include soft-deleted.

        Returns:
            Matching company records.
        """
        clauses: list[str] = []
        params: list[object] = []
        param_idx = 0

        if not include_removed:
            clauses.append("(ats_vendor IS NULL OR ats_vendor != 'removed')")
        if vendor:
            param_idx += 1
            clauses.append(f"ats_vendor = ${param_idx}")
            params.append(vendor)

        where = " AND ".join(clauses) if clauses else "1=1"
        pool = self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                f"SELECT * FROM companies WHERE {where} ORDER BY slug",
                *params,
            )
        return [_company_from_record(r) for r in rows]

    async def mark_company_removed(self, slug: str) -> bool:
        """Soft-delete via ats_vendor='removed'.

        Args:
            slug: Company slug.

        Returns:
            True if a tracked, not-already-removed company was matched.
        """
        pool = self._get_pool()
        async with pool.acquire() as conn:
            result = await conn.execute(
                """UPDATE companies SET
                       ats_vendor = 'removed', ats_override = 0,
                       last_verified_at = NULL
                   WHERE slug = $1
                     AND ats_vendor IS DISTINCT FROM 'removed'""",
                slug,
            )
        # asyncpg returns "UPDATE N" where N is the row count
        return not result.endswith(" 0")

    async def bump_discover_failure(self, slug: str) -> int:
        """Increment consecutive discover-failure counter.

        Args:
            slug: Company slug.

        Returns:
            New failure count.
        """
        pool = self._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """UPDATE companies
                   SET consecutive_discover_failures =
                       consecutive_discover_failures + 1
                   WHERE slug = $1
                   RETURNING consecutive_discover_failures""",
                slug,
            )
        return int(row["consecutive_discover_failures"]) if row else 0

    async def reset_discover_failures(self, slug: str) -> None:
        """Zero the discover-failure counter.

        Args:
            slug: Company slug.
        """
        pool = self._get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE companies SET consecutive_discover_failures = 0 WHERE slug = $1",
                slug,
            )

    async def record_enrichment(
        self,
        *,
        job_id: str,
        jd_text: str,
        jd_quality: str,
        enriched_at: datetime,
        enrich_source: str,
        jd_lang: str | None = None,
        posted_at: datetime | None = None,
    ) -> None:
        """Stamp a job as enriched with JD body and quality.

        Args:
            job_id: Store-assigned job identity.
            jd_text: JD body text.
            jd_quality: Quality band string.
            enriched_at: Enrichment timestamp.
            enrich_source: Source label.
            jd_lang: Optional detected language.
            posted_at: Optional JD-derived posting date. Fills the column
                only when it is NULL (COALESCE keeps the stored value) — an
                exact card-derived date is never overwritten by this
                approximate relative-text date. None leaves the column
                untouched.
        """
        pool = self._get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """UPDATE jobs SET
                       jd_text = $1, jd_quality = $2, enriched_at = $3,
                       enrich_source = $4, jd_lang = $5, enrich_error = NULL,
                       closed_at = NULL,
                       posted_at = COALESCE(posted_at, $7),
                       -- An enrichment replaces the JD with new content, so the
                       -- ml_gate_* verdict (computed against the OLD JD) is
                       -- stale: a stale 'fail' is excluded from the funnel
                       -- forever and a stale 'pass' is trusted without
                       -- re-gating. This is an unconditional content change, so
                       -- NULL the verdict columns to force a re-gate (matching
                       -- the unconditional enrich_error/closed_at reset above
                       -- and save_job's input-change reset).
                       ml_gate_score = NULL, ml_gate_result = NULL,
                       ml_gate_fail_reason = NULL, ml_gate_at = NULL,
                       ml_gate_version = NULL
                   WHERE id = $6""",
                jd_text,
                jd_quality,
                enriched_at,
                enrich_source,
                jd_lang,
                int(job_id),
                posted_at,
            )

    async def list_unenriched_jobs(
        self,
        *,
        platform: str,
        limit: int,
    ) -> list[UnenrichedJob]:
        """List open jobs on a platform that still have no JD text.

        Args:
            platform: Source platform to scope the listing to.
            limit: Maximum rows to return.

        Returns:
            Rows with jd_text IS NULL and closed_at IS NULL, newest
            discovered_at first (id breaks ties). Empty when none match.
        """
        pool = self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """SELECT id, canonical_id, url FROM jobs
                   WHERE platform = $1
                     AND jd_text IS NULL
                     AND closed_at IS NULL
                   ORDER BY discovered_at DESC, id DESC
                   LIMIT $2""",
                platform,
                limit,
            )
        return [
            UnenrichedJob(
                job_id=str(row["id"]),
                canonical_id=row["canonical_id"],
                url=row["url"],
            )
            for row in rows
        ]

    async def mark_job_closed(
        self,
        *,
        job_id: str,
        closed_at: datetime,
        reason: str | None = None,
    ) -> None:
        """Stamp a single job as closed (posting confirmed gone).

        Args:
            job_id: Store-assigned job identity.
            closed_at: Closure timestamp to set.
            reason: Optional marker recording WHY the row was closed, stamped
                into enrich_error for ops triage (house convention, e.g.
                'gone:{status}:{vendor}', 'backfill:stale-no-jd'). None
                leaves enrich_error untouched (COALESCE keeps the stored
                value).
        """
        pool = self._get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """UPDATE jobs
                   SET closed_at = $1,
                       enrich_error = COALESCE($3, enrich_error)
                   WHERE id = $2""",
                closed_at,
                int(job_id),
                reason,
            )

    async def enrich_paste(
        self,
        *,
        platform: str,
        canonical_id: str,
        jd_text: str,
    ) -> str:
        """Manual JD paste fallback.

        Args:
            platform: Source platform.
            canonical_id: Platform-specific identity.
            jd_text: Pasted JD text.

        Returns:
            Store-assigned job identity.

        Raises:
            ValueError: If job not found.
        """
        pool = self._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT id FROM jobs WHERE platform = $1 AND canonical_id = $2",
                platform,
                canonical_id,
            )
        if row is None:
            raise ValueError(f"job not found: {platform}/{canonical_id}")
        job_id = str(row["id"])
        quality = assess_quality(jd_text)
        await self.record_enrichment(
            job_id=job_id,
            jd_text=jd_text,
            jd_quality=quality.value,
            enriched_at=datetime.now(tz=UTC),
            enrich_source="manual-paste",
        )
        return job_id

    async def get_state(self, key: str) -> str | None:
        """Read a key-value state entry.

        Args:
            key: State key.

        Returns:
            Value if found, else None.
        """
        pool = self._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT value FROM state WHERE key = $1",
                key,
            )
        return str(row["value"]) if row else None

    async def set_state(self, key: str, value: str) -> None:
        """Write a key-value state entry.

        Args:
            key: State key.
            value: State value.
        """
        pool = self._get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO state (key, value) VALUES ($1, $2)
                   ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value""",
                key,
                value,
            )

    async def record_cost(self, *, day: str, spent_usd: float, calls: int = 1) -> None:
        """Upsert daily cost ledger spend and attempted call count.

        Args:
            day: YYYY-MM-DD date string.
            spent_usd: Cost to accumulate.
            calls: Attempted LLM calls to accumulate.
        """
        pool = self._get_pool()
        async with pool.acquire() as conn:
            await self._record_cost_conn(
                conn, day=day, spent_usd=spent_usd, calls=calls
            )

    async def get_cost(self, day: str) -> CostEntry | None:
        """Read a single day's cost entry.

        Args:
            day: YYYY-MM-DD date.

        Returns:
            Cost entry if found, else None.
        """
        pool = self._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM cost_ledger WHERE day = $1",
                day,
            )
        if row is None:
            return None
        return CostEntry(
            day=str(row["day"]),
            spent_usd=float(row["spent_usd"]),
            calls=int(row["calls"]),
            last_updated=row["last_updated"],
        )

    async def get_cost_range(self, *, since_days: int = 30) -> list[CostEntry]:
        """Read cost entries within a date range.

        Args:
            since_days: Days to look back.

        Returns:
            Cost entries ordered by day descending.
        """
        pool = self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """SELECT * FROM cost_ledger
                   WHERE day::date >= CURRENT_DATE - $1::int
                   ORDER BY day DESC""",
                since_days,
            )
        return [
            CostEntry(
                day=str(r["day"]),
                spent_usd=float(r["spent_usd"]),
                calls=int(r["calls"]),
                last_updated=r["last_updated"],
            )
            for r in rows
        ]

    async def digest_stats(self, *, threshold: int = 60) -> DigestStats:
        """Aggregate counts for digest footer.

        Args:
            threshold: Score threshold for filtered_count.

        Returns:
            Digest statistics.
        """
        pool = self._get_pool()
        async with pool.acquire() as conn:
            total = await self._count(conn, "SELECT COUNT(*) FROM jobs")
            scored_today = await self._count(
                conn,
                "SELECT COUNT(*) FROM jobs"
                " WHERE discovered_at >= CURRENT_DATE"
                " AND discovered_at < CURRENT_DATE + INTERVAL '1 day'",
            )
            stage_b_evaluated = await self._count(
                conn,
                "SELECT COUNT(*) FROM evaluations WHERE stage_b_status = 'completed'",
            )
            filtered_count = await self._count(
                conn,
                """SELECT COUNT(*) FROM evaluations
                   WHERE stage_b_status = 'completed'
                     AND (stage_b_fit_json->>'score_0_100')::integer >= $1""",
                threshold,
            )
            cost_row = await conn.fetchrow(
                "SELECT calls, spent_usd FROM cost_ledger "
                "WHERE day = CURRENT_DATE::text",
            )
        llm_calls = int(cost_row["calls"]) if cost_row else 0
        cost_usd = float(cost_row["spent_usd"]) if cost_row else 0.0
        return DigestStats(
            total_jobs=total,
            scored_today=scored_today,
            stage_b_evaluated=stage_b_evaluated,
            filtered_count=filtered_count,
            llm_calls_today=llm_calls,
            total_cost_today_usd=cost_usd,
        )

    async def needs_attention(
        self,
        *,
        days: int = 7,
        max_per_category: int = 10,
    ) -> AttentionReport:
        """Surface pipeline health concerns.

        Args:
            days: Look-back window.
            max_per_category: Max items per category.

        Returns:
            Attention report.
        """
        pool = self._get_pool()
        async with pool.acquire() as conn:
            enrich_rows = await conn.fetch(
                """SELECT id, title, company, enrich_error FROM jobs
                   WHERE enrich_error IS NOT NULL
                     AND discovered_at >= now() - ($1 || ' days')::interval
                   LIMIT $2""",
                str(days),
                max_per_category,
            )

            low_q_rows = await conn.fetch(
                """SELECT j.id, j.title, j.company, j.jd_quality FROM jobs j
                   JOIN evaluations e ON e.job_id = j.id
                   WHERE j.jd_quality IN ('stub', 'partial')
                     AND e.stage_a_status = 'completed'
                     AND j.discovered_at >= now() - ($1 || ' days')::interval
                   LIMIT $2""",
                str(days),
                max_per_category,
            )

            # Jobs stuck in error past the retry cap (any age — no longer retried).
            stuck_rows = await conn.fetch(
                """SELECT j.id, j.title, j.company,
                          e.stage_a_error_count, e.stage_b_error_count
                   FROM jobs j
                   JOIN evaluations e ON e.job_id = j.id
                   WHERE e.stage_a_error_count >= $1
                      OR e.stage_b_error_count >= $1
                   LIMIT $2""",
                MAX_STAGE_RETRIES,
                max_per_category,
            )

        enrich_errors = [
            AttentionItem(
                job_id=str(r["id"]),
                title=str(r["title"]),
                company=str(r["company"]),
                category="enrich_error",
                detail=str(r["enrich_error"]),
            )
            for r in enrich_rows
        ]
        low_quality = [
            AttentionItem(
                job_id=str(r["id"]),
                title=str(r["title"]),
                company=str(r["company"]),
                category="low_quality_scored",
                detail=f"quality={r['jd_quality']}",
            )
            for r in low_q_rows
        ]
        stuck_scoring = [
            AttentionItem(
                job_id=str(r["id"]),
                title=str(r["title"]),
                company=str(r["company"]),
                category="stuck_scoring",
                detail=(
                    f"stage_a_errors={r['stage_a_error_count']}, "
                    f"stage_b_errors={r['stage_b_error_count']}"
                ),
            )
            for r in stuck_rows
        ]
        return AttentionReport(
            enrich_errors=enrich_errors,
            low_quality_scored=low_quality,
            stuck_scoring=stuck_scoring,
        )

    async def mark_stale_jobs_closed(
        self,
        *,
        older_than_days: int,
        dry_run: bool,
    ) -> int:
        """Close stale jobs that have no usable JD and have not been closed yet.

        Targets rows where jd_quality IS NULL or IN ('missing', 'abandoned'),
        discovered_at is older than the given threshold, and closed_at IS NULL.

        Args:
            older_than_days: Discovery-age threshold (exclusive).
            dry_run: When True, count matching rows without writing.

        Returns:
            Row count: matched rows (dry_run=True) or updated rows (dry_run=False).

        Raises:
            ValueError: If older_than_days < 1 (would select fresh/all rows).
        """
        if older_than_days < 1:
            raise ValueError("older_than_days must be >= 1")
        _STALE_WHERE = """
            (jd_quality IS NULL OR jd_quality IN ('missing', 'abandoned'))
            AND discovered_at < now() - make_interval(days => $1)
            AND closed_at IS NULL
        """
        pool = self._get_pool()
        async with pool.acquire() as conn:
            if dry_run:
                return await self._count(
                    conn,
                    f"SELECT COUNT(*) FROM jobs WHERE {_STALE_WHERE}",
                    older_than_days,
                )
            result = await conn.execute(
                f"""UPDATE jobs
                    SET closed_at = now(),
                        enrich_error = $2
                    WHERE {_STALE_WHERE}""",
                older_than_days,
                _STALE_BACKFILL_MARKER,
            )
        # asyncpg returns "UPDATE N"
        return int(result.split()[-1])

    async def record_llm_usage(self, usage: LLMUsage) -> None:
        """Record a single LLM call's usage metrics.

        Args:
            usage: LLM usage metrics for one call.
        """
        pool = self._get_pool()
        async with pool.acquire() as conn:
            await self._record_llm_usage_conn(conn, usage)

    async def record_llm_usage_with_cost(
        self,
        *,
        day: str,
        spent_usd: float,
        usage: LLMUsage,
    ) -> None:
        """Atomically record a usage row and same-call ledger spend.

        Args:
            day: YYYY-MM-DD cost ledger day.
            spent_usd: Cost to accumulate.
            usage: LLM usage metrics for one call.
        """
        pool = self._get_pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                await self._record_llm_usage_conn(conn, usage)
                await self._record_cost_conn(
                    conn,
                    day=day,
                    spent_usd=spent_usd,
                    calls=0,
                )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    async def _record_cost_conn(
        conn: asyncpg.Connection,
        *,
        day: str,
        spent_usd: float,
        calls: int,
    ) -> None:
        await conn.execute(
            """INSERT INTO cost_ledger (day, spent_usd, calls)
               VALUES ($1, $2, $3)
               ON CONFLICT (day) DO UPDATE SET
                   spent_usd = cost_ledger.spent_usd + EXCLUDED.spent_usd,
                   calls = cost_ledger.calls + EXCLUDED.calls,
                   last_updated = now()""",
            day,
            spent_usd,
            calls,
        )

    @staticmethod
    async def _record_llm_usage_conn(
        conn: asyncpg.Connection,
        usage: LLMUsage,
    ) -> None:
        await conn.execute(
            """INSERT INTO llm_usage (model, input_tokens, output_tokens, cost_usd,
               cached, latency_ms, timestamp, job_id, stage, run_id)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)""",
            usage.model,
            usage.input_tokens,
            usage.output_tokens,
            usage.cost_usd,
            usage.cached,
            usage.latency_ms,
            usage.timestamp,
            int(usage.job_id) if usage.job_id is not None else None,
            usage.stage,
            usage.run_id,
        )

    @staticmethod
    async def _count(
        conn: asyncpg.Connection,
        sql: str,
        *args: object,
    ) -> int:
        """Execute a COUNT query and return the integer result.

        Args:
            conn: Active asyncpg connection.
            sql: SQL query that returns a single COUNT(*) value.
            *args: Query parameters.

        Returns:
            Integer count.
        """
        row = await conn.fetchrow(sql, *args)
        return int(row[0]) if row else 0
