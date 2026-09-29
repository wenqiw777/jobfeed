"""Capabilities required for bounded intermediary attribution."""

from typing import Protocol

from jobfeed.domain.models import JobPosting
from jobfeed.ports.store import JobStore
from jobfeed.ports.store_ops import StoreOpsMixin


class IntermediaryStore(JobStore, StoreOpsMixin, Protocol):
    """Store with indexed, bounded title candidate lookup."""

    async def official_candidates(self, title: str) -> list[tuple[str, JobPosting]]:
        """Return up to 201 parent/source candidates for an exact normalized title.

        Args:
            title: Source title to normalize before querying.

        Returns:
            Parent IDs and postings. 201 rows signals an unsafe overflow.
        """
        ...

    async def employer_ats_urls(self, company: str) -> list[str]:
        """Return bounded known URLs for the named employer.

        Args:
            company: Public employer name.

        Returns:
            Existing URLs; no inferred or model-generated hostnames.
        """
        ...

    async def resolve_real_job_ids(self, source_ids: list[str]) -> list[str]:
        """Resolve distinct canonical owners for saved source IDs.

        Args:
            source_ids: Store-assigned source IDs.

        Returns:
            Existing unique parent IDs.
        """
        ...
