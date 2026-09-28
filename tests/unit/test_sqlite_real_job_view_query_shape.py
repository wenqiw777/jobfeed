"""Regression checks for bounded canonical Results SQL."""

from datetime import UTC, datetime

from jobfeed.adapters.store import _sqlite_real_job_views as views


def test_results_reads_materialized_canonical_dates() -> None:
    """List queries do not aggregate source dates during every request."""
    assert views.SQLITE_CANONICAL_DATE == "r.canonical_posted_at"
    assert views.SQLITE_FIRST_SEEN == "r.first_discovered_at"
    assert "SELECT MIN" not in views.SQLITE_CANONICAL_DATE


def test_require_verdict_bounds_work_to_evaluated_or_review_jobs() -> None:
    """The candidate subquery prevents a full canonical-parent scan."""
    _, _, shared_where, *_ = views._real_job_predicate(
        "results",
        None,
        True,
        None,
        datetime.now(UTC),
    )

    assert "SELECT real_job_id FROM real_job_evaluations" in shared_where
    assert "UNION SELECT id FROM real_jobs" in shared_where
