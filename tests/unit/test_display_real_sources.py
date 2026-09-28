"""Frozen real-source acceptance cases, including explicit known false negatives."""

import itertools
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from jobfeed.domain.dedupe import pick_display_representatives
from jobfeed.domain.models import JobPosting

FIXTURE = json.loads(
    (Path(__file__).parents[1] / "fixtures" / "jd_display_real_posts.json").read_text()
)


@pytest.mark.parametrize(
    "case",
    [
        pytest.param(
            case,
            id=case["name"],
            marks=pytest.mark.xfail(
                strict=True,
                reason="Cross-source miss: formatting, summary or identity gap",
            )
            if case["known_failure"]
            else (),
        )
        for case in FIXTURE["cases"]
    ],
)
def test_real_source_display_equivalence(case):
    jobs = [
        JobPosting(
            **{**FIXTURE["posts"][identifier], "id": identifier},
            discovered_at=datetime(2026, 9, 19, tzinfo=UTC),
        )
        for identifier in case["ids"]
    ]
    # Grouping must not depend on whether ATS, LinkedIn or Jobright comes first.
    for ordering in itertools.permutations(jobs):
        assert (
            len(pick_display_representatives(ordering, {})) == case["expected_groups"]
        ), case["evidence"]


@pytest.mark.xfail(
    strict=True, reason="A native-linked summary blocks content grouping"
)
def test_three_sources_after_controlling_for_linkedin_formatting():
    """Controlled variant: even fixing whitespace alone leaves an order bug."""
    ids = ["2156", "440986", "469210"]
    jobs = [
        JobPosting(
            **{**FIXTURE["posts"][identifier], "id": identifier},
            discovered_at=datetime(2026, 9, 19, tzinfo=UTC),
        )
        for identifier in ids
    ]
    jobs[-1].jd_text = jobs[0].jd_text
    counts = {
        len(pick_display_representatives(ordering, {}))
        for ordering in itertools.permutations(jobs)
    }
    assert counts == {1}
