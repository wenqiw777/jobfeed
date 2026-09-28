"""PostgreSQL query construction retains pending distinct posts, without IO."""

from jobfeed.adapters.store.postgres import _append_unrated_completed_suppression


def test_unrated_exclusion_only_uses_own_evaluation() -> None:
    conditions: list[str] = []
    _append_unrated_completed_suppression(conditions)
    assert conditions == [
        "(evaluations.job_id IS NULL "
        "OR evaluations.stage_a_status IS DISTINCT FROM 'completed')"
    ]
