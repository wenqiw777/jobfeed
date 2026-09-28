"""Shared posting-age date selection, independent of archive policy."""

from datetime import UTC, datetime

from jobfeed.domain.models import JobPosting


def effective_job_date(job: JobPosting, *, now: datetime | None = None) -> datetime:
    """Use a nonfuture posting date, otherwise the persisted first discovery.

    Naive timestamps follow the existing UTC convention. This does not mutate
    source timestamps, choose an archive threshold, or reconstruct history.

    Args:
        job: Posting to inspect.
        now: Current timestamp used for age or retry calculations.

    Returns:
        Posting date when valid, otherwise first discovery date.
    """
    reference = _utc(now or datetime.now(UTC))
    if job.posted_at is not None:
        posted = _utc(job.posted_at)
        if posted <= reference:
            return posted
    return _utc(job.discovered_at)


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
