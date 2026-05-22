"""SQL statements used by the SQLiteStore adapter."""

from __future__ import annotations

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
    stage_a_prompt_hash, stage_a_resume_hash
) VALUES (?, ?, ?, ?, 'completed', NULL, ?, ?, ?, ?)
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
    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
"""

SAVE_STAGE_A_ERROR_SQL = """
INSERT INTO evaluations (job_id, stage_a_status, stage_a_error)
VALUES (?, 'error', ?)
ON CONFLICT(job_id) DO UPDATE SET
    stage_a_status = excluded.stage_a_status,
    stage_a_error = excluded.stage_a_error,
    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
"""

SAVE_STAGE_B_SQL = """
INSERT INTO evaluations (
    job_id, stage_b_verdict, stage_b_jd_summary, stage_b_verdict_json,
    stage_b_summary_json, stage_b_fit_json, stage_b_hooks_json,
    stage_b_status, stage_b_error, stage_b_model,
    stage_b_cost_usd, stage_b_prompt_hash, stage_b_resume_hash
) VALUES (?, ?, ?, ?, ?, ?, ?, 'completed', NULL, ?, ?, ?, ?)
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
    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
"""

SAVE_STAGE_B_ERROR_SQL = """
INSERT INTO evaluations (job_id, stage_b_status, stage_b_error)
VALUES (?, 'error', ?)
ON CONFLICT(job_id) DO UPDATE SET
    stage_b_status = excluded.stage_b_status,
    stage_b_error = excluded.stage_b_error,
    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
"""

PENDING_STAGE_A_SQL = """
SELECT jobs.*
FROM jobs
LEFT JOIN evaluations ON evaluations.job_id = jobs.id
WHERE evaluations.job_id IS NULL OR evaluations.stage_a_status IS NULL
ORDER BY jobs.discovered_at DESC, jobs.id DESC
LIMIT ?
"""

PENDING_STAGE_B_SQL = """
SELECT jobs.*
FROM jobs
JOIN evaluations ON evaluations.job_id = jobs.id
WHERE evaluations.stage_a_status = 'completed'
  AND (evaluations.stage_b_status IS NULL OR evaluations.stage_b_status = 'error')
ORDER BY jobs.discovered_at DESC, jobs.id DESC
LIMIT ?
"""

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
    "PENDING_STAGE_A_SQL",
    "PENDING_STAGE_B_SQL",
    "SAVE_STAGE_A_ERROR_SQL",
    "SAVE_STAGE_A_SQL",
    "SAVE_STAGE_B_ERROR_SQL",
    "SAVE_STAGE_B_SQL",
    "UPDATE_JOB_SQL",
]
