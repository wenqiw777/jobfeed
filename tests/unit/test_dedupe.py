"""Unit tests for the pure twin-clustering + representative primitive."""

from __future__ import annotations

from datetime import UTC, datetime

from jobfeed.domain.dedupe import (
    TwinCluster,
    cluster_twins,
    pick_representatives,
    twin_key,
)
from jobfeed.domain.models import JobPosting, QualityBand

_DISCOVERED = datetime(2026, 5, 29, 12, 0, tzinfo=UTC)

_TWO_CLUSTERS = 2
_THREE_CLUSTERS = 3
_FOUR_MEMBERS = 4
_TWO_REPS = 2


def _job(
    *,
    canonical_id: str,
    platform: str = "greenhouse",
    company: str = "Stripe",
    title: str = "Backend Engineer",
    jd_quality: QualityBand | None = QualityBand.FULL,
    posted_at: datetime | None = None,
    discovered_at: datetime = _DISCOVERED,
) -> JobPosting:
    """Build a JobPosting with dedup-relevant fields and inert defaults."""
    return JobPosting(
        platform=platform,
        canonical_id=canonical_id,
        url=f"https://example.com/{platform}/{canonical_id}",
        title=title,
        company=company,
        location="Remote",
        discovered_at=discovered_at,
        jd_quality=jd_quality,
        posted_at=posted_at,
    )


# ---------------------------------------------------------------------------
# twin_key
# ---------------------------------------------------------------------------


def test_twin_key_folds_corporate_suffix_variants() -> None:
    """'Stripe' / 'Stripe, Inc.' / 'Stripe Technologies' share one company key."""
    plain = twin_key(_job(canonical_id="a", company="Stripe"))
    incorporated = twin_key(_job(canonical_id="b", company="Stripe, Inc."))
    technologies = twin_key(_job(canonical_id="c", company="Stripe Technologies"))

    assert plain == incorporated == technologies
    assert plain[0] == "stripe"


def test_twin_key_combines_company_and_title() -> None:
    """The key is (normalized company, normalized title)."""
    assert twin_key(_job(canonical_id="a", company="Acme Corp", title="ML Eng")) == (
        "acme",
        "ml eng",
    )


# ---------------------------------------------------------------------------
# cluster_twins — grouping
# ---------------------------------------------------------------------------


def test_same_job_across_platforms_forms_one_cluster() -> None:
    """N postings of one real job across platforms → ONE cluster with N members."""
    jobs = [
        _job(canonical_id="gh-1", platform="greenhouse"),
        _job(canonical_id="sa-1", platform="speedyapply"),
        _job(canonical_id="li-1", platform="linkedin"),
        _job(canonical_id="ij-1", platform="indeed"),
    ]

    clusters = cluster_twins(jobs)

    assert len(clusters) == 1
    assert len(clusters[0].members) == _FOUR_MEMBERS


def test_distinct_jobs_stay_in_separate_clusters() -> None:
    """Different title or company keeps postings in separate clusters."""
    jobs = [
        _job(canonical_id="a", company="Stripe", title="Backend Engineer"),
        _job(canonical_id="b", company="Stripe", title="Frontend Engineer"),
        _job(canonical_id="c", company="Datadog", title="Backend Engineer"),
    ]

    clusters = cluster_twins(jobs)

    assert len(clusters) == _THREE_CLUSTERS
    assert all(len(cluster.members) == 1 for cluster in clusters)


# ---------------------------------------------------------------------------
# cluster_twins — empty-norm guard
# ---------------------------------------------------------------------------


def test_blank_company_rows_do_not_fold_together() -> None:
    """Two UNRELATED blank-company rows each form their own singleton cluster."""
    jobs = [
        _job(canonical_id="x", company="???", title="Backend Engineer"),
        _job(canonical_id="y", company="!!!", title="Backend Engineer"),
    ]

    clusters = cluster_twins(jobs)

    assert len(clusters) == _TWO_CLUSTERS
    assert all(len(cluster.members) == 1 for cluster in clusters)


def test_blank_title_rows_do_not_fold_together() -> None:
    """Blank-title rows are also isolated even with an identical company."""
    jobs = [
        _job(canonical_id="x", company="Stripe", title="###"),
        _job(canonical_id="y", company="Stripe", title="%%%"),
    ]

    clusters = cluster_twins(jobs)

    assert len(clusters) == _TWO_CLUSTERS


def test_blank_norm_row_does_not_fold_with_real_row() -> None:
    """A blank-company row never joins a real cluster sharing the title."""
    jobs = [
        _job(canonical_id="real", company="Stripe", title="Backend Engineer"),
        _job(canonical_id="blank", company="", title="Backend Engineer"),
    ]

    clusters = cluster_twins(jobs)

    assert len(clusters) == _TWO_CLUSTERS


# ---------------------------------------------------------------------------
# Representative rule — key 1: JD quality (all six bands)
# ---------------------------------------------------------------------------


def test_representative_prefers_higher_quality_stub_beats_missing() -> None:
    """Quality key ranks all six bands: a STUB twin beats a MISSING twin."""
    missing = _job(canonical_id="m", jd_quality=QualityBand.MISSING)
    stub = _job(canonical_id="s", jd_quality=QualityBand.STUB)

    clusters = cluster_twins([missing, stub])

    assert len(clusters) == 1
    assert clusters[0].representative.canonical_id == "s"


def test_representative_full_beats_abandoned_and_none() -> None:
    """FULL outranks ABANDONED and a None quality across the full ladder."""
    none_q = _job(canonical_id="n", jd_quality=None)
    abandoned = _job(canonical_id="ab", jd_quality=QualityBand.ABANDONED)
    full = _job(canonical_id="f", jd_quality=QualityBand.FULL)

    clusters = cluster_twins([none_q, abandoned, full])

    assert clusters[0].representative.canonical_id == "f"


# ---------------------------------------------------------------------------
# Representative rule — key 2: source priority
# ---------------------------------------------------------------------------


def test_source_priority_greenhouse_beats_linkedin_at_equal_quality() -> None:
    """At equal quality, an ATS-family (greenhouse) twin beats a linkedin twin."""
    linkedin = _job(canonical_id="li", platform="linkedin", jd_quality=QualityBand.FULL)
    greenhouse = _job(
        canonical_id="gh", platform="greenhouse", jd_quality=QualityBand.FULL
    )

    clusters = cluster_twins([linkedin, greenhouse])

    assert clusters[0].representative.platform == "greenhouse"


def test_source_priority_full_ladder_order() -> None:
    """speedyapply > linkedin > linkedin_guest > indeed at equal quality."""
    indeed = _job(canonical_id="i", platform="indeed", jd_quality=QualityBand.FULL)
    li_guest = _job(
        canonical_id="lg", platform="linkedin_guest", jd_quality=QualityBand.FULL
    )
    linkedin = _job(canonical_id="li", platform="linkedin", jd_quality=QualityBand.FULL)
    speedy = _job(
        canonical_id="sa", platform="speedyapply", jd_quality=QualityBand.FULL
    )

    clusters = cluster_twins([indeed, li_guest, linkedin, speedy])

    assert clusters[0].representative.platform == "speedyapply"


def test_source_priority_linkedin_guest_beats_indeed() -> None:
    """A same-quality linkedin_guest twin outranks its indeed twin (rank 3 < 4)."""
    indeed = _job(canonical_id="i2", platform="indeed", jd_quality=QualityBand.FULL)
    li_guest = _job(
        canonical_id="lg2", platform="linkedin_guest", jd_quality=QualityBand.FULL
    )

    clusters = cluster_twins([indeed, li_guest])

    assert clusters[0].representative.platform == "linkedin_guest"


def test_source_priority_historical_linkedin_jobspy_beats_indeed() -> None:
    """A historical linkedin_jobspy twin keeps its tier-3 rank over indeed.

    The linkedin_jobspy SOURCE is removed, but rows persisted before the
    removal must not fall to the unknown-platform rank.
    """
    indeed = _job(canonical_id="i3", platform="indeed", jd_quality=QualityBand.FULL)
    li_jobspy = _job(
        canonical_id="lj", platform="linkedin_jobspy", jd_quality=QualityBand.FULL
    )

    clusters = cluster_twins([indeed, li_jobspy])

    assert clusters[0].representative.platform == "linkedin_jobspy"


def test_unknown_platform_sorts_last() -> None:
    """An unknown platform loses the source-priority key to a known one."""
    unknown = _job(canonical_id="u", platform="mystery", jd_quality=QualityBand.FULL)
    indeed = _job(canonical_id="i", platform="indeed", jd_quality=QualityBand.FULL)

    clusters = cluster_twins([unknown, indeed])

    assert clusters[0].representative.platform == "indeed"


def test_no_ats_literal_platform_value_used() -> None:
    """There is no "ats" platform; an "ats"-tagged row sorts as unknown-last."""
    fake_ats = _job(canonical_id="x", platform="ats", jd_quality=QualityBand.FULL)
    greenhouse = _job(
        canonical_id="gh", platform="greenhouse", jd_quality=QualityBand.FULL
    )

    clusters = cluster_twins([fake_ats, greenhouse])

    assert clusters[0].representative.platform == "greenhouse"


# ---------------------------------------------------------------------------
# Representative rule — key 3: recency (posted_at NULLS-LAST, not discovered_at)
# ---------------------------------------------------------------------------


def test_recency_uses_posted_at_not_discovered_at() -> None:
    """With equal quality+platform, newer posted_at wins; discovered_at is equal."""
    shared_discovered = datetime(2026, 5, 29, 8, 0, tzinfo=UTC)
    older = _job(
        canonical_id="old",
        posted_at=datetime(2026, 5, 1, tzinfo=UTC),
        discovered_at=shared_discovered,
    )
    newer = _job(
        canonical_id="new",
        posted_at=datetime(2026, 5, 20, tzinfo=UTC),
        discovered_at=shared_discovered,
    )

    clusters = cluster_twins([older, newer])

    assert clusters[0].representative.canonical_id == "new"


def test_recency_none_posted_at_sorts_last() -> None:
    """A posting with posted_at=None loses to one with any posted_at (NULLS-LAST)."""
    no_date = _job(canonical_id="none", posted_at=None)
    dated = _job(canonical_id="dated", posted_at=datetime(2026, 1, 1, tzinfo=UTC))

    clusters = cluster_twins([no_date, dated])

    assert clusters[0].representative.canonical_id == "dated"


# ---------------------------------------------------------------------------
# Representative rule — key 4: stable final tiebreak
# ---------------------------------------------------------------------------


def test_stable_tiebreak_on_platform_and_canonical_id() -> None:
    """All earlier keys equal → deterministic (platform, canonical_id) winner."""
    a = _job(canonical_id="aaa", platform="greenhouse", posted_at=None)
    b = _job(canonical_id="bbb", platform="greenhouse", posted_at=None)

    forward = cluster_twins([a, b])[0].representative.canonical_id
    reversed_ = cluster_twins([b, a])[0].representative.canonical_id

    assert forward == reversed_ == "aaa"


# ---------------------------------------------------------------------------
# pick_representatives
# ---------------------------------------------------------------------------


def test_pick_representatives_returns_one_per_cluster() -> None:
    """pick_representatives yields exactly one posting per cluster."""
    jobs = [
        _job(canonical_id="gh-1", platform="greenhouse", company="Stripe"),
        _job(canonical_id="sa-1", platform="speedyapply", company="Stripe"),
        _job(canonical_id="dd-1", platform="greenhouse", company="Datadog"),
    ]

    reps = pick_representatives(jobs)

    assert len(reps) == _TWO_REPS
    assert {rep.company for rep in reps} == {"Stripe", "Datadog"}


def test_cluster_exposes_key_and_is_frozen() -> None:
    """TwinCluster carries its twin key and is immutable (frozen dataclass)."""
    cluster = cluster_twins([_job(canonical_id="a", company="Stripe")])[0]

    assert isinstance(cluster, TwinCluster)
    assert cluster.key == ("stripe", "backend engineer")
    try:
        cluster.representative = cluster.members[0]  # type: ignore[misc]
    except AttributeError:
        pass
    else:  # pragma: no cover - frozen dataclass must reject mutation
        raise AssertionError("TwinCluster should be frozen")
