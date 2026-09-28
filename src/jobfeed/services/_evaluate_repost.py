"""Recheck source evidence at model boundaries without changing prior scores."""

from jobfeed.domain.models import JobPosting
from jobfeed.ports.store import JobStore


async def confirmed_repost(store: JobStore, job: JobPosting) -> bool:
    if job.is_repost is True:
        return True
    return await stored_repost(store, job.id) if job.id is not None else False


async def stored_repost(store: JobStore, job_id: str) -> bool:
    current = await store.get_job(job_id)
    return current is not None and current.is_repost is True


async def eligible_jobs(store: JobStore, jobs: list[JobPosting]) -> list[JobPosting]:
    return [job for job in jobs if not await confirmed_repost(store, job)]
