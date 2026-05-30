"""Phase 4 twin/dedupe contract test.

Locks the twin-key shape and the four-key representative-selection rule
(Decision 8). Each case isolates ONE decisive key so that changing the order or
semantics of any key — quality, source priority, recency, or the stable
tiebreak — makes this contract fail deliberately. The empty-norm singleton
guard is locked here too.
"""

from __future__ import annotations

from datetime import UTC, datetime

from jobfeed.domain.dedupe import cluster_twins, pick_representatives, twin_key
from jobfeed.domain.models import JobPosting, QualityBand

_DISCOVERED = datetime(2026, 5, 29, 0, 0, tzinfo=UTC)
_OLD = datetime(2026, 5, 1, 0, 0, tzinfo=UTC)
_NEW = datetime(2026, 5, 20, 0, 0, tzinfo=UTC)

_TWO_CLUSTERS = 2


def _job(
    *,
    canonical_id: str,
    platform: str,
    company: str = "Stripe",
    title: str = "Backend Engineer",
    jd_quality: QualityBand | None,
    posted_at: datetime | None,
) -> JobPosting:
    """Build a posting pinning only dedup-relevant fields."""
    return JobPosting(
        platform=platform,
        canonical_id=canonical_id,
        url=f"https://example.com/{platform}/{canonical_id}",
        title=title,
        company=company,
        location="Remote",
        discovered_at=_DISCOVERED,
        jd_quality=jd_quality,
        posted_at=posted_at,
    )


def test_twin_key_contract() -> None:
    """twin_key is exactly (normalized company, normalized title)."""
    job = _job(
        canonical_id="a",
        platform="greenhouse",
        company="Stripe, Inc.",
        title="Senior Backend Engineer",
        jd_quality=QualityBand.FULL,
        posted_at=_NEW,
    )

    assert twin_key(job) == ("stripe", "senior backend engineer")


def test_key1_quality_is_first_decisive_key() -> None:
    """Quality wins even when a lower-quality twin has higher source priority."""
    # greenhouse (top priority) but STUB; linkedin (lower priority) but FULL.
    greenhouse_stub = _job(
        canonical_id="gh",
        platform="greenhouse",
        jd_quality=QualityBand.STUB,
        posted_at=_NEW,
    )
    linkedin_full = _job(
        canonical_id="li",
        platform="linkedin",
        jd_quality=QualityBand.FULL,
        posted_at=_OLD,
    )

    clusters = cluster_twins([greenhouse_stub, linkedin_full])

    assert len(clusters) == 1
    assert clusters[0].representative.canonical_id == "li"


def test_key2_source_priority_breaks_quality_tie() -> None:
    """At equal quality, the ATS family outranks linkedin (and 'ats' is unknown)."""
    fake_ats = _job(
        canonical_id="ats",
        platform="ats",
        jd_quality=QualityBand.FULL,
        posted_at=_NEW,
    )
    linkedin = _job(
        canonical_id="li",
        platform="linkedin",
        jd_quality=QualityBand.FULL,
        posted_at=_NEW,
    )
    greenhouse = _job(
        canonical_id="gh",
        platform="greenhouse",
        jd_quality=QualityBand.FULL,
        posted_at=_OLD,
    )

    rep = cluster_twins([fake_ats, linkedin, greenhouse])[0].representative

    assert rep.platform == "greenhouse"


def test_key3_recency_breaks_quality_and_priority_tie() -> None:
    """Equal quality+platform → newer posted_at wins; None sorts last."""
    older = _job(
        canonical_id="old",
        platform="greenhouse",
        jd_quality=QualityBand.FULL,
        posted_at=_OLD,
    )
    newer = _job(
        canonical_id="new",
        platform="greenhouse",
        jd_quality=QualityBand.FULL,
        posted_at=_NEW,
    )
    undated = _job(
        canonical_id="undated",
        platform="greenhouse",
        jd_quality=QualityBand.FULL,
        posted_at=None,
    )

    rep = cluster_twins([undated, older, newer])[0].representative

    assert rep.canonical_id == "new"


def test_key4_stable_tiebreak_is_platform_then_canonical_id() -> None:
    """All earlier keys equal → smallest (platform, canonical_id) wins, order-free."""
    a = _job(
        canonical_id="aaa",
        platform="greenhouse",
        jd_quality=QualityBand.FULL,
        posted_at=None,
    )
    b = _job(
        canonical_id="bbb",
        platform="greenhouse",
        jd_quality=QualityBand.FULL,
        posted_at=None,
    )

    assert cluster_twins([a, b])[0].representative.canonical_id == "aaa"
    assert cluster_twins([b, a])[0].representative.canonical_id == "aaa"


def test_empty_norm_guard_contract() -> None:
    """Unrelated blank-company rows never fold; each is its own singleton."""
    blank_a = _job(
        canonical_id="x",
        platform="indeed",
        company="@@@",
        jd_quality=QualityBand.MISSING,
        posted_at=None,
    )
    blank_b = _job(
        canonical_id="y",
        platform="indeed",
        company="###",
        jd_quality=QualityBand.MISSING,
        posted_at=None,
    )

    clusters = cluster_twins([blank_a, blank_b])

    assert len(clusters) == _TWO_CLUSTERS
    assert all(len(cluster.members) == 1 for cluster in clusters)


def test_pick_representatives_contract() -> None:
    """pick_representatives returns exactly one representative per cluster."""
    jobs = [
        _job(
            canonical_id="gh",
            platform="greenhouse",
            jd_quality=QualityBand.FULL,
            posted_at=_NEW,
        ),
        _job(
            canonical_id="sa",
            platform="speedyapply",
            jd_quality=QualityBand.FULL,
            posted_at=_NEW,
        ),
    ]

    reps = pick_representatives(jobs)

    assert len(reps) == 1
    assert reps[0].canonical_id == "gh"
