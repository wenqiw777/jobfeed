"""As-of-bound PostgreSQL aggregate goldens for backend parity."""

from __future__ import annotations

from datetime import datetime

from jobfeed.adapters.migration._pg_baseline_reader import PostgresBaselineReader


def capture_canonical_aggregates(
    reader: PostgresBaselineReader, as_of: datetime
) -> dict[str, object]:
    """Capture rolling aggregates against one explicit database cutoff.

    Args:
        reader: Active repeatable-read baseline reader.
        as_of: Database-derived aware timestamp shared by every query.

    Returns:
        Ordered canonical rows and pending counts for hashing.
    """
    pending_stage_a = reader.scalar(
        "SELECT COUNT(*) FROM jobs LEFT JOIN evaluations e ON e.job_id=jobs.id "
        "WHERE jobs.closed_at IS NULL "
        "AND (e.job_id IS NULL OR e.stage_a_status IS NULL "
        "OR (e.stage_a_status='error' AND e.stage_a_error_count < 3))"
    )
    pending_stage_b = reader.scalar(
        "SELECT COUNT(*) FROM jobs JOIN evaluations e ON e.job_id=jobs.id "
        "WHERE e.stage_a_status='completed' "
        "AND (e.stage_b_status IS NULL OR "
        "(e.stage_b_status='error' AND e.stage_b_error_count < 3))"
    )
    needs_attention = {
        "enrich_errors": reader.rows(
            "SELECT id AS job_id, title, company, enrich_error AS detail FROM jobs "
            "WHERE enrich_error IS NOT NULL "
            "AND discovered_at >= %s - INTERVAL '30 days' ORDER BY id",
            (as_of,),
        ),
        "low_quality_scored": reader.rows(
            "SELECT j.id AS job_id, j.title, j.company, j.jd_quality AS detail "
            "FROM jobs j JOIN evaluations e ON e.job_id=j.id "
            "WHERE j.jd_quality IN ('stub','partial') "
            "AND e.stage_a_status='completed' "
            "AND j.discovered_at >= %s - INTERVAL '30 days' ORDER BY j.id",
            (as_of,),
        ),
        "stuck_scoring": reader.rows(
            "SELECT j.id AS job_id, j.title, j.company, "
            "e.stage_a_error_count, e.stage_b_error_count "
            "FROM jobs j JOIN evaluations e ON e.job_id=j.id "
            "WHERE e.stage_a_error_count >= 3 OR e.stage_b_error_count >= 3 "
            "ORDER BY j.id"
        ),
    }
    funnel = reader.rows(
        "SELECT run_id, jobs_filtered+jobs_ml_gated+jobs_seniority_filtered+"
        "GREATEST(jobs_gate_passed,stage_a_scored,stage_b_scored) total_candidates, "
        "jobs_ml_gated+jobs_seniority_filtered+"
        "GREATEST(jobs_gate_passed,stage_a_scored,stage_b_scored) "
        "after_filter, GREATEST(jobs_gate_passed,stage_a_scored,stage_b_scored) "
        "after_gate, GREATEST(stage_a_scored,stage_b_scored) scored "
        "FROM pipeline_runs WHERE source='evaluate' "
        "AND started_at >= %s - INTERVAL '30 days' "
        "ORDER BY started_at DESC, run_id DESC",
        (as_of,),
    )
    daily_cost = reader.rows(
        "SELECT day, spent_usd, calls, last_updated FROM cost_ledger "
        "WHERE day::date >= (%s AT TIME ZONE 'UTC')::date - 30 "
        "ORDER BY day DESC",
        (as_of,),
    )
    llm_percentiles = reader.rows(
        "SELECT date(timestamp) AS day, "
        "percentile_cont(0.5) WITHIN GROUP (ORDER BY latency_ms) AS p50, "
        "percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms) AS p95, "
        "avg(input_tokens) AS avg_in, avg(output_tokens) AS avg_out "
        "FROM llm_usage WHERE timestamp >= %s - INTERVAL '30 days' "
        "GROUP BY 1 ORDER BY 1",
        (as_of,),
    )
    return {
        "as_of_utc": as_of,
        "window_days": 30,
        "pending_stage_a": pending_stage_a,
        "pending_stage_b": pending_stage_b,
        "needs_attention": needs_attention,
        "funnel": funnel,
        "daily_cost": daily_cost,
        "llm_percentiles": llm_percentiles,
    }
