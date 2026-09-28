from datetime import datetime
from types import SimpleNamespace

from jobfeed.domain.models import JobPosting
from jobfeed.services._jobs_view_sort import LIBRARY_SORT_KEYS


def row(id, at, score, repost=False):
    return SimpleNamespace(
        job=JobPosting(
            id=str(id),
            platform="linkedin",
            canonical_id=str(id),
            url="https://example.test",
            title="SWE",
            company="Example",
            location="US",
            discovered_at=datetime.fromisoformat(at),
            is_repost=repost,
        ),
        stage_b_fit_score=score,
        stage_a_score=None,
    )


def test_score_band_repost_demotion_does_not_cross_bands():
    rows = [
        row(1, "2026-09-20T12:00:00+00:00", 99, True),
        row(2, "2026-09-20T12:00:00+00:00", 91),
        row(3, "2026-09-20T12:00:00+00:00", 89),
        row(4, "2026-09-20T12:00:00+00:00", 100),
        row(5, "2026-09-20T12:00:00+00:00", None),
    ]
    assert [
        r.job.id for r in sorted(rows, key=LIBRARY_SORT_KEYS["triage_score_desc"])
    ] == ["4", "2", "1", "3", "5"]


def test_first_seen_uses_detroit_calendar_day_and_repost_last():
    rows = [
        row(1, "2026-09-20T03:59:00+00:00", 80, True),
        row(2, "2026-09-19T12:00:00+00:00", 80),
        row(3, "2026-09-20T04:01:00+00:00", 80, True),
    ]
    assert [
        r.job.id for r in sorted(rows, key=LIBRARY_SORT_KEYS["triage_posted_desc"])
    ] == ["3", "2", "1"]
    assert [
        r.job.id for r in sorted(rows, key=LIBRARY_SORT_KEYS["triage_posted_asc"])
    ] == ["2", "1", "3"]
