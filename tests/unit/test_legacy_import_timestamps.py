"""Legacy evaluation imports preserve completion-time semantics."""

from __future__ import annotations

from jobfeed.adapters.store.legacy_import import _map_evaluation_row


def test_completed_legacy_stages_receive_stable_approximate_times() -> None:
    """Legacy created/updated clocks backfill missing Stage A/B event clocks."""
    row = {
        "job_id": 7,
        "stage_a_score": 80,
        "stage_a_one_line": "fit",
        "timing_eligible": "yes",
        "stage_a_status": "completed",
        "stage_a_error": None,
        "stage_a_model": "model-a",
        "stage_a_cost_usd": 0.1,
        "stage_a_prompt_hash": "a",
        "resume_hash": "resume",
        "stage_b_verdict": "apply",
        "stage_b_jd_summary": "summary",
        "block_a_verdict": "{}",
        "block_b_jd_summary": "{}",
        "block_c_fit_analysis": "{}",
        "block_e_resume_hooks": "{}",
        "stage_b_status": "completed",
        "stage_b_error": None,
        "stage_b_model": "model-b",
        "stage_b_cost_usd": 0.2,
        "stage_b_prompt_hash": "b",
        "created_at": "2026-05-02T03:04:05.000000Z",
        "updated_at": "2026-05-03T04:05:06.000000Z",
    }

    mapped = _map_evaluation_row(row)

    assert mapped["stage_a_at"] == row["created_at"]
    assert mapped["stage_b_at"] == row["updated_at"]
