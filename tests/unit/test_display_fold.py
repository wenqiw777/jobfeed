"""Unit tests for the Phase 8 status-aware display fold.

``pick_display_representatives`` layers a status-priority class AHEAD of the
Decision 8 representative key: an in-flight application twin ({applied,
interviewing, offer}) beats a shortlist-tier twin ({shortlisted,
awaiting_referral}) beats everything else; ties inside one class fall through
to the existing ``_representative_sort_key``. ``pick_representatives`` stays
status-blind (guarded here and by the Phase 4 dedupe contract test).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from jobfeed.domain.dedupe import pick_display_representatives, pick_representatives
from jobfeed.domain.models import JobPosting, QualityBand
from jobfeed.domain.models_views import JobsViewQuery

_DISCOVERED = datetime(2026, 6, 1, 0, 0, tzinfo=UTC)


def _job(
    *,
    id: str | None,
    platform: str,
    company: str = "Stripe",
    title: str = "Backend Engineer",
    jd_quality: QualityBand | None = None,
) -> JobPosting:
    """Build a posting pinning only fold-relevant fields."""
    return JobPosting(
        id=id,
        platform=platform,
        canonical_id=f"c-{id or 'none'}",
        url=f"https://example.com/{platform}/{id or 'none'}",
        title=title,
        company=company,
        location="Remote",
        discovered_at=_DISCOVERED,
        jd_quality=jd_quality,
    )


def _better_and_worse_twins() -> tuple[JobPosting, JobPosting]:
    """Return twins where the FIRST wins on the Decision 8 key.

    The first is greenhouse FULL (best quality + best source priority); the
    second is indeed STUB (worse on both).

    Returns:
        Tuple of (better-key twin, worse-key twin).
    """
    better = _job(id="1", platform="greenhouse", jd_quality=QualityBand.FULL)
    worse = _job(id="2", platform="indeed", jd_quality=QualityBand.STUB)
    return better, worse


@pytest.mark.parametrize("status", ["applied", "interviewing", "offer"])
def test_application_twin_beats_better_decision8_twin(status: str) -> None:
    """An in-flight application twin wins despite a worse Decision 8 key."""
    better, worse = _better_and_worse_twins()

    reps = pick_display_representatives([better, worse], {"2": status})

    assert reps == [worse]


def test_equal_status_class_falls_back_to_existing_key() -> None:
    """With equal status classes the Decision 8 key decides, order-free."""
    better, worse = _better_and_worse_twins()
    statuses = {"1": "new", "2": "new"}

    assert pick_display_representatives([better, worse], statuses) == [better]
    assert pick_display_representatives([worse, better], statuses) == [better]


def test_application_class_beats_shortlist_class() -> None:
    """{applied,...} outranks {shortlisted, awaiting_referral}."""
    better, worse = _better_and_worse_twins()

    reps = pick_display_representatives(
        [better, worse],
        {"1": "shortlisted", "2": "applied"},
    )

    assert reps == [worse]


def test_shortlist_class_beats_rest() -> None:
    """{shortlisted, awaiting_referral} outranks plain statuses like new."""
    better, worse = _better_and_worse_twins()

    reps = pick_display_representatives(
        [better, worse],
        {"1": "new", "2": "awaiting_referral"},
    )

    assert reps == [worse]


def test_missing_status_takes_lowest_priority_class() -> None:
    """Ids absent from the mapping (or id-less rows) get the lowest class."""
    better, worse = _better_and_worse_twins()
    idless = _job(id=None, platform="lever", jd_quality=QualityBand.FULL)

    reps = pick_display_representatives(
        [better, idless, worse],
        {"2": "shortlisted"},
    )

    assert reps == [worse]


def test_empty_input_returns_empty_list() -> None:
    """No postings → no representatives."""
    assert pick_display_representatives([], {}) == []


def test_single_job_is_its_own_representative() -> None:
    """A singleton cluster returns its only member."""
    job = _job(id="1", platform="greenhouse", jd_quality=QualityBand.FULL)

    assert pick_display_representatives([job], {}) == [job]


def test_clusters_preserve_first_seen_order() -> None:
    """One representative per cluster, in first-seen cluster order."""
    stripe = _job(id="1", platform="greenhouse", jd_quality=QualityBand.FULL)
    datadog = _job(
        id="2",
        platform="greenhouse",
        company="Datadog",
        jd_quality=QualityBand.FULL,
    )

    reps = pick_display_representatives([stripe, datadog], {"2": "applied"})

    assert reps == [stripe, datadog]


def test_pick_representatives_stays_status_blind() -> None:
    """Guard rail: the Phase 5 primitive ignores statuses for the same input."""
    better, worse = _better_and_worse_twins()

    # The display fold flips the winner; the scoring-funnel primitive must not.
    assert pick_display_representatives([better, worse], {"2": "applied"}) == [worse]
    assert pick_representatives([better, worse]) == [better]


def test_jobs_view_query_rejects_unknown_tab() -> None:
    """JobsViewQuery validates the tab value against the six known tabs."""
    with pytest.raises(ValueError, match="tab"):
        JobsViewQuery(tab="inbox")


def test_jobs_view_query_rejects_negative_pagination() -> None:
    """JobsViewQuery rejects a negative limit or offset."""
    with pytest.raises(ValueError, match="limit"):
        JobsViewQuery(tab="all", limit=-1)
    with pytest.raises(ValueError, match="offset"):
        JobsViewQuery(tab="all", offset=-1)
