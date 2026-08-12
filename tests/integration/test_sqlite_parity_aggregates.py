"""Golden non-empty business aggregates for SQLite cutover parity."""

from __future__ import annotations

from pathlib import Path

from jobfeed.adapters.migration._pg_baseline_manifest import aggregate_manifest
from jobfeed.adapters.migration.sqlite_parity import verify_sqlite_parity
from tests.integration.test_sqlite_parity_verifier import (
    _manifest_for,
    _target_and_manifest,
)


async def test_nonempty_business_aggregates_match_postgres_shapes(
    tmp_path: Path,
) -> None:
    """Attention, funnel, cost, and percentile goldens all reproduce exactly."""
    lifecycle, _ = await _target_and_manifest(tmp_path)
    async with lifecycle.connection() as connection:
        await connection.execute(
            "UPDATE jobs SET jd_quality='partial', enrich_error='boom' WHERE id=1"
        )
        await connection.execute(
            """INSERT INTO evaluations(
                   id, job_id, stage_a_status, stage_a_error_count
               ) VALUES(1, 1, 'completed', 3)"""
        )
        await connection.execute(
            """INSERT INTO pipeline_runs(
                   id, run_id, started_at, source, status, jobs_filtered,
                   jobs_ml_gated, jobs_gate_passed, stage_a_scored, stage_b_scored
               ) VALUES(1, 'run-1', '2026-08-10T00:00:00.000000Z', 'evaluate',
                        'completed', 2, 3, 4, 5, 1)"""
        )
        await connection.execute(
            """INSERT INTO cost_ledger(day, spent_usd, calls, last_updated)
               VALUES('2026-08-11', 1.5, 2, '2026-08-11T01:00:00.000000Z')"""
        )
        await connection.executemany(
            """INSERT INTO llm_usage(
                   id, model, input_tokens, output_tokens, cost_usd, cached,
                   latency_ms, timestamp
               ) VALUES(?, 'model', ?, ?, 0.1, 0, ?, ?)""",
            [
                (1, 10, 20, 100, "2026-08-11T01:00:00.000000Z"),
                (2, 30, 40, 300, "2026-08-11T02:00:00.000000Z"),
            ],
        )
        await connection.executemany(
            "INSERT INTO state(key, value) VALUES(?, ?)",
            [("é", "accent"), ("Z", "ascii")],
        )
    manifest = await _manifest_for(lifecycle)
    manifest["aggregates"] = aggregate_manifest(_aggregate_golden())

    report = await verify_sqlite_parity(lifecycle, manifest, chunk_size=1)

    assert report.is_match
    assert report.aggregates is not None
    assert report.aggregates.pending_stage_a == 0
    assert report.aggregates.pending_stage_b == 1
    await lifecycle.close()


def _aggregate_golden() -> dict[str, object]:
    return {
        "as_of_utc": "2026-08-12T00:00:00.000000Z",
        "window_days": 30,
        "pending_stage_a": 0,
        "pending_stage_b": 1,
        "needs_attention": {
            "enrich_errors": [
                {
                    "job_id": 1,
                    "title": "Engineer",
                    "company": "Acme",
                    "detail": "boom",
                }
            ],
            "low_quality_scored": [
                {
                    "job_id": 1,
                    "title": "Engineer",
                    "company": "Acme",
                    "detail": "partial",
                }
            ],
            "stuck_scoring": [
                {
                    "job_id": 1,
                    "title": "Engineer",
                    "company": "Acme",
                    "stage_a_error_count": 3,
                    "stage_b_error_count": 0,
                }
            ],
        },
        "funnel": [
            {
                "run_id": "run-1",
                "total_candidates": 10,
                "after_filter": 8,
                "after_gate": 5,
                "scored": 5,
            }
        ],
        "daily_cost": [
            {
                "day": "2026-08-11",
                "spent_usd": 1.5,
                "calls": 2,
                "last_updated": "2026-08-11T01:00:00.000000Z",
            }
        ],
        "llm_percentiles": [
            {
                "day": "2026-08-11",
                "p50": 200.0,
                "p95": 290.0,
                "avg_in": 20.0,
                "avg_out": 30.0,
            }
        ],
    }
