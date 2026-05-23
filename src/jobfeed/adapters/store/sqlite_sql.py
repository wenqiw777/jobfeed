"""SQL statements used by the SQLiteStore adapter."""

from __future__ import annotations

from datetime import datetime, timedelta

from jobfeed.domain.scoring import MAX_STAGE_RETRIES

INSERT_JOB_DO_NOTHING_SQL = """
INSERT INTO jobs (
    platform, canonical_id, url, title, company, location, jd_text, jd_quality,
    posted_at, discovered_at, enriched_at, enrich_source,
    company_norm, title_norm, location_norm
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(platform, canonical_id) DO NOTHING
"""

UPDATE_JOB_SQL = """
UPDATE jobs
SET platform = ?, canonical_id = ?, url = ?, title = ?, company = ?,
    location = ?, jd_text = COALESCE(?, jd_text),
    jd_quality = COALESCE(?, jd_quality), posted_at = COALESCE(?, posted_at),
    discovered_at = ?, enriched_at = COALESCE(?, enriched_at),
    enrich_source = COALESCE(?, enrich_source),
    company_norm = ?, title_norm = ?, location_norm = ?
WHERE id = ?
"""

SAVE_STAGE_A_SQL = """
INSERT INTO evaluations (
    job_id, stage_a_score, stage_a_one_line, stage_a_timing_eligible,
    stage_a_status, stage_a_error, stage_a_model, stage_a_cost_usd,
    stage_a_prompt_hash, stage_a_resume_hash, stage_a_at
) VALUES (
    ?, ?, ?, ?, 'completed', NULL, ?, ?, ?, ?,
    strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
)
ON CONFLICT(job_id) DO UPDATE SET
    stage_a_score = excluded.stage_a_score,
    stage_a_one_line = excluded.stage_a_one_line,
    stage_a_timing_eligible = excluded.stage_a_timing_eligible,
    stage_a_status = excluded.stage_a_status,
    stage_a_error = NULL,
    stage_a_model = excluded.stage_a_model,
    stage_a_cost_usd = excluded.stage_a_cost_usd,
    stage_a_prompt_hash = excluded.stage_a_prompt_hash,
    stage_a_resume_hash = excluded.stage_a_resume_hash,
    stage_a_at = COALESCE(stage_a_at, strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
"""

SAVE_STAGE_A_ERROR_SQL = """
INSERT INTO evaluations (job_id, stage_a_status, stage_a_error, stage_a_error_count)
VALUES (?, 'error', ?, 1)
ON CONFLICT(job_id) DO UPDATE SET
    stage_a_status = excluded.stage_a_status,
    stage_a_error = excluded.stage_a_error,
    stage_a_error_count = stage_a_error_count + 1,
    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
"""

SAVE_STAGE_B_SQL = """
INSERT INTO evaluations (
    job_id, stage_b_verdict, stage_b_jd_summary, stage_b_verdict_json,
    stage_b_summary_json, stage_b_fit_json, stage_b_hooks_json,
    stage_b_status, stage_b_error, stage_b_model,
    stage_b_cost_usd, stage_b_prompt_hash, stage_b_resume_hash, stage_b_at
) VALUES (
    ?, ?, ?, ?, ?, ?, ?, 'completed', NULL, ?, ?, ?, ?,
    strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
)
ON CONFLICT(job_id) DO UPDATE SET
    stage_b_verdict = excluded.stage_b_verdict,
    stage_b_jd_summary = excluded.stage_b_jd_summary,
    stage_b_verdict_json = excluded.stage_b_verdict_json,
    stage_b_summary_json = excluded.stage_b_summary_json,
    stage_b_fit_json = excluded.stage_b_fit_json,
    stage_b_hooks_json = excluded.stage_b_hooks_json,
    stage_b_status = excluded.stage_b_status,
    stage_b_error = NULL,
    stage_b_model = excluded.stage_b_model,
    stage_b_cost_usd = excluded.stage_b_cost_usd,
    stage_b_prompt_hash = excluded.stage_b_prompt_hash,
    stage_b_resume_hash = excluded.stage_b_resume_hash,
    stage_b_at = COALESCE(stage_b_at, strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
"""

SAVE_STAGE_B_ERROR_SQL = """
INSERT INTO evaluations (job_id, stage_b_status, stage_b_error, stage_b_error_count)
VALUES (?, 'error', ?, 1)
ON CONFLICT(job_id) DO UPDATE SET
    stage_b_status = excluded.stage_b_status,
    stage_b_error = excluded.stage_b_error,
    stage_b_error_count = stage_b_error_count + 1,
    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
"""

_PENDING_STAGE_A_BASE = (
    "SELECT jobs.*\nFROM jobs\nLEFT JOIN evaluations ON evaluations.job_id = jobs.id"
)
_PENDING_STAGE_B_BASE = (
    "SELECT jobs.*\nFROM jobs\nJOIN evaluations ON evaluations.job_id = jobs.id"
)
_ORDER_LIMIT = "ORDER BY jobs.discovered_at DESC, jobs.id DESC\nLIMIT ?"

# Error rows over MAX_STAGE_RETRIES stay terminally in 'error' status and drop
# out of the pending queues (plan Task 1); they surface in needs_attention for
# manual triage instead of retrying (and burning LLM calls) forever.
_STAGE_A_UNDER_CAP = (
    f"(evaluations.stage_a_status IS NOT 'error' "
    f"OR evaluations.stage_a_error_count < {MAX_STAGE_RETRIES})"
)
_STAGE_B_UNDER_CAP = (
    f"(evaluations.stage_b_status IS NOT 'error' "
    f"OR evaluations.stage_b_error_count < {MAX_STAGE_RETRIES})"
)


def _corpus_condition(corpus: str) -> str:
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


def build_pending_stage_a_query(
    *,
    limit: int,
    quality_bands: frozenset[str] | None,
    corpus: str,
    max_days: int | None,
    now: datetime,
) -> tuple[str, list[object]]:
    """Build the Stage A pending query with optional legacy parity filters.

    Args:
        limit: Max rows.
        quality_bands: Optional jd_quality allow-list.
        corpus: "unrated", "all", or "failed".
        max_days: Optional freshness window on discovered_at.
        now: Reference time for the freshness cutoff.

    Returns:
        SQL string and its positional parameters.
    """
    conditions: list[str] = []
    params: list[object] = []
    corpus_clause = _corpus_condition(corpus)
    if corpus_clause:
        conditions.append(corpus_clause)
    conditions.append(_STAGE_A_UNDER_CAP)
    if quality_bands:
        marks = ", ".join("?" for _ in quality_bands)
        conditions.append(f"jobs.jd_quality IN ({marks})")
        params.extend(sorted(quality_bands))
    if max_days is not None:
        conditions.append("jobs.discovered_at >= ?")
        params.append((now - timedelta(days=max_days)).isoformat())
    where = " AND ".join(conditions) if conditions else "1 = 1"
    params.append(limit)
    return f"{_PENDING_STAGE_A_BASE}\nWHERE {where}\n{_ORDER_LIMIT}", params


def build_pending_stage_b_query(
    *,
    limit: int,
    max_days: int | None,
    now: datetime,
) -> tuple[str, list[object]]:
    """Build the Stage B pending query (Stage A completed, Stage B pending).

    Args:
        limit: Max rows.
        max_days: Optional freshness window on discovered_at.
        now: Reference time for the freshness cutoff.

    Returns:
        SQL string and its positional parameters.
    """
    conditions = [
        "evaluations.stage_a_status = 'completed'",
        "(evaluations.stage_b_status IS NULL OR evaluations.stage_b_status = 'error')",
        _STAGE_B_UNDER_CAP,
    ]
    params: list[object] = []
    if max_days is not None:
        conditions.append("jobs.discovered_at >= ?")
        params.append((now - timedelta(days=max_days)).isoformat())
    params.append(limit)
    where = " AND ".join(conditions)
    return f"{_PENDING_STAGE_B_BASE}\nWHERE {where}\n{_ORDER_LIMIT}", params


LIST_EVALUATED_SQL = """
SELECT
    jobs.id,
    jobs.platform,
    jobs.canonical_id,
    jobs.url,
    jobs.title,
    jobs.company,
    jobs.location,
    jobs.jd_text,
    jobs.jd_quality,
    jobs.posted_at,
    jobs.discovered_at,
    jobs.enriched_at,
    jobs.enrich_source,
    evaluations.stage_a_score,
    evaluations.stage_a_one_line,
    evaluations.stage_a_timing_eligible,
    evaluations.stage_a_status,
    evaluations.stage_a_error,
    evaluations.stage_a_model,
    evaluations.stage_a_cost_usd,
    evaluations.stage_a_prompt_hash,
    evaluations.stage_a_resume_hash,
    evaluations.stage_b_verdict,
    evaluations.stage_b_jd_summary,
    evaluations.stage_b_verdict_json,
    evaluations.stage_b_summary_json,
    evaluations.stage_b_fit_json,
    evaluations.stage_b_hooks_json,
    evaluations.stage_b_status,
    evaluations.stage_b_error,
    evaluations.stage_b_model,
    evaluations.stage_b_cost_usd,
    evaluations.stage_b_prompt_hash,
    evaluations.stage_b_resume_hash
FROM jobs
JOIN evaluations ON evaluations.job_id = jobs.id
ORDER BY jobs.discovered_at DESC, jobs.id DESC
LIMIT ?
"""

INSERT_PIPELINE_RUN_SQL = """
INSERT INTO pipeline_runs (
    run_id, started_at, source, jobs_discovered, jobs_inserted, jobs_updated,
    jobs_filtered, jobs_ml_gated, stage_a_scored, stage_b_scored, jobs_scored,
    total_llm_cost_usd, errors, finished_at
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

__all__ = [
    "INSERT_JOB_DO_NOTHING_SQL",
    "INSERT_PIPELINE_RUN_SQL",
    "LIST_EVALUATED_SQL",
    "SAVE_STAGE_A_ERROR_SQL",
    "SAVE_STAGE_A_SQL",
    "SAVE_STAGE_B_ERROR_SQL",
    "SAVE_STAGE_B_SQL",
    "UPDATE_JOB_SQL",
    "build_pending_stage_a_query",
    "build_pending_stage_b_query",
]
