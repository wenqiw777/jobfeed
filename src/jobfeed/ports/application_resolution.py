"""Application-link evidence writes and streaming source boundaries."""

from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Protocol, runtime_checkable

from jobfeed.domain.models import JobPosting
from jobfeed.ports.source import SourceFetchProgressCallback

PostingBatchCallback = Callable[[list[JobPosting]], Awaitable[None]]


@runtime_checkable
class StreamingApplicationSource(Protocol):
    """Publish completed source batches before the source finishes fetching."""

    async def fetch_jobs_streaming(
        self,
        config: dict[str, object],
        on_progress: SourceFetchProgressCallback,
        on_batch: PostingBatchCallback,
    ) -> list[JobPosting]:
        """Return collected postings, publishing each usable batch first.

        Args:
            config: Source scan options.
            on_progress: Source progress observer.
            on_batch: Async consumer for each observed posting batch.

        Returns:
            All collected source postings.
        """
        ...


class ApplicationIdentityStore(Protocol):
    """Retain observed route outcomes with atomic canonical evidence updates."""

    async def get_job(self, job_id: str) -> JobPosting | None:
        """Read the current source posting, never a cached parent identity.

        Args:
            job_id: Persisted source row ID.

        Returns:
            Current posting, or None when absent.
        """
        ...

    async def get_state(self, key: str) -> str | None:
        """Read a previous resolution outcome.

        Args:
            key: Resolution state key.

        Returns:
            Saved state value, or None when absent.
        """
        ...

    async def record_application_identity(
        self,
        *,
        job_id: str,
        expected_apply_url: str | None,
        ats_url: str | None,
        state_key: str,
        state_value: str,
        run_id: str | None = None,
        owner_id: str | None = None,
        generation: int | None = None,
    ) -> bool:
        """Apply evidence and outcome only if the source Apply URL is unchanged.

        Args:
            job_id: Persisted source row ID.
            expected_apply_url: Observed Apply URL to compare at commit.
            ats_url: Verified concrete ATS job URL, if resolved.
            state_key: Resolution state key.
            state_value: Serialized outcome and verification facts.
            run_id: Optional scan run owning the write.
            owner_id: Optional lease owner.
            generation: Optional lease generation.

        Returns:
            Whether the source still matches and the update committed.
        """
        ...


class ApplicationBackfillStore(ApplicationIdentityStore, Protocol):
    """Snapshot historical sources before owned application-route resolution."""

    async def list_application_backfill_ids(
        self, since: datetime | None = None
    ) -> list[str]:
        """Read open candidates in stable priority order.

        Args:
            since: Inclusive posting cutoff; unknown dates use first discovery.

        Returns:
            Existing source IDs; no new source rows are created.
        """
        ...
