"""Posting age prefers a valid source date, never a future date."""

from datetime import UTC, datetime, timedelta

import pytest

from jobfeed.domain.filtering import HardFilters, apply_hard_filters
from jobfeed.domain.job_priority import freshness_value
from tests.support.factories import make_job


@pytest.mark.parametrize("posted", [None, datetime(2026, 10, 1, tzinfo=UTC)])
def test_missing_or_future_posted_time_uses_first_discovery(posted):
    now = datetime(2026, 9, 19, tzinfo=UTC)
    job = make_job(posted_at=posted, discovered_at=now - timedelta(days=40))
    assert apply_hard_filters(job, HardFilters(posted_within_days=30), now=now)
    assert freshness_value(job, now=now) == freshness_value(
        make_job(posted_at=None, discovered_at=job.discovered_at), now=now
    )


def test_old_posting_is_old_even_when_just_discovered():
    now = datetime(2026, 9, 19, tzinfo=UTC)
    job = make_job(posted_at=now - timedelta(days=40), discovered_at=now)
    assert apply_hard_filters(job, HardFilters(posted_within_days=30), now=now)


def test_naive_timestamps_follow_utc_convention():
    now = datetime(2026, 9, 19)
    job = make_job(posted_at=now - timedelta(days=40), discovered_at=now)
    assert apply_hard_filters(job, HardFilters(posted_within_days=30), now=now)
