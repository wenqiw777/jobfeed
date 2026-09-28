"""Recheck source evidence at model boundaries without changing prior scores."""

from jobfeed.domain.models import JobPosting
from jobfeed.ports.store import JobStore


async def confirmed_repost(store: JobStore, job: JobPosting) -> bool:
    """Check both supplied and persisted repost evidence.

    Args:
        store: Store providing the state or posting reads required by this operation.
        job: Source posting to inspect or persist.

    Returns:
        Whether the supplied posting or its current stored row confirms a repost.
    """
    if job.is_repost is True:
        return True
    return await stored_repost(store, job.id) if job.id is not None else False


async def stored_repost(store: JobStore, job_id: str) -> bool:
    """Read the latest stored repost flag.

    Args:
        store: Store providing the state or posting reads required by this operation.
        job_id: Source posting identifier.

    Returns:
        Whether the existing stored posting explicitly confirms a repost.
    """
    current = await store.get_job(job_id)
    return current is not None and current.is_repost is True


async def eligible_jobs(store: JobStore, jobs: list[JobPosting]) -> list[JobPosting]:
    """Exclude postings with confirmed repost evidence before evaluation.

    Args:
        store: Store providing the state or posting reads required by this operation.
        jobs: Source postings in input order.

    Returns:
        Input postings whose current evidence does not confirm a repost.
    """
    return [job for job in jobs if not await confirmed_repost(store, job)]
