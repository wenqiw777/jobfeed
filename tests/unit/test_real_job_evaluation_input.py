"""One scoring input is selected for each verified real job."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

from jobfeed.domain.models import JobPosting, QualityBand
from jobfeed.domain.real_job_evaluation import (
    conflicting_complete_sources,
    input_facts_json,
    legacy_evaluation_input_hold,
    select_real_job_input,
)

NOW = datetime(2026, 9, 24, tzinfo=UTC)


def _source(
    source_id: str,
    *,
    platform: str = "linkedin",
    url: str | None = None,
    jd: str = "Build production software services and maintain APIs. " * 8,
    quality: QualityBand = QualityBand.FULL,
) -> JobPosting:
    return JobPosting(
        id=source_id,
        platform=platform,
        canonical_id=source_id,
        url=url or f"https://www.linkedin.com/jobs/view/{source_id}/",
        title="Software Engineer",
        company="Example",
        location="Dallas, TX",
        discovered_at=NOW,
        posted_at=NOW - timedelta(days=3),
        jd_text=jd,
        jd_quality=quality,
    )


def test_authoritative_ats_jd_wins_without_alias_repost_or_closure() -> None:
    linked = replace(
        _source("2"),
        is_repost=True,
        closed_at=NOW,
        discovered_at=NOW - timedelta(days=1),
    )
    ats = _source(
        "1",
        platform="jobright",
        url="https://careers.southwestair.com/us/en/job/REQ123/software-engineer",
        jd=linked.jd_text,
    )
    selected = select_real_job_input("7", [linked, ats], now=NOW)
    assert selected.real_job_id == "7"
    assert selected.source_job_id == "1"
    assert selected.job.jd_text == ats.jd_text
    assert selected.job.discovered_at == NOW - timedelta(days=1)
    assert selected.job.closed_at is None
    assert selected.job.is_repost is not True


def test_quality_then_stable_source_id_selects_one_input() -> None:
    partial = replace(
        _source("3", url="https://example.com/role/3"), jd_quality=QualityBand.PARTIAL
    )
    good = replace(
        _source("2", url="https://example.com/role/2"), jd_quality=QualityBand.GOOD
    )
    full = _source("1")
    selected = select_real_job_input("9", [partial, good, full], now=NOW)
    assert selected.source_job_id == "1"


def test_conflicting_substantive_requirements_hold_scoring() -> None:
    first = _source("1", jd="Build Java services and APIs. " * 15)
    second = _source("2", jd="Design FPGA circuits and verify RTL. " * 15)
    assert select_real_job_input("5", [first, second], now=NOW) is None


def test_empty_sources_hold_scoring() -> None:
    assert select_real_job_input("5", [], now=NOW) is None


def test_original_posted_date_is_a_scoring_input_fact() -> None:
    source = _source("1")
    earlier = replace(source, posted_at=NOW - timedelta(days=10))
    first = select_real_job_input("5", [source], now=NOW)
    second = select_real_job_input("5", [earlier], now=NOW)
    assert input_facts_json(first) != input_facts_json(second)


def test_official_input_wins_over_divergent_aggregator() -> None:
    official = _source("1", url="https://boards.greenhouse.io/acme/jobs/1234567")
    aggregator = _source("2", jd="Build unrelated FPGA circuits and verify RTL. " * 15)
    selected = select_real_job_input("1", [official, aggregator], now=NOW)
    assert selected is not None
    assert selected.source_job_id == "1"
    assert conflicting_complete_sources([official, aggregator]) is None


def test_conflicting_official_inputs_stay_held() -> None:
    first = _source("1", url="https://boards.greenhouse.io/acme/jobs/1234567")
    second = _source(
        "2",
        url="https://jobs.lever.co/acme/12345678-1234-1234-1234-123456789012",
        jd="Build FPGA circuits and verify RTL. " * 15,
    )
    assert select_real_job_input("1", [first, second], now=NOW) is None


def test_legacy_score_time_uncertainty_does_not_require_review() -> None:
    for enriched in (None, NOW + timedelta(days=1)):
        source = replace(_source("1"), enriched_at=enriched)
        selected = select_real_job_input("1", [source], now=NOW)
        assert legacy_evaluation_input_hold(selected, [source], [(1, NOW)]) is None
