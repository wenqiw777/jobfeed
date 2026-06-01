"""Job source port protocols and session result types."""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable

from jobfeed.domain.models import JobPosting, QualityBand


@dataclass(kw_only=True)
class DiscoverResult:
    """Result of discovering source postings before optional enrichment."""

    postings: list[JobPosting]
    needs_reauth: bool = False
    error: str | None = None
    duration_s: float = 0.0


@dataclass(kw_only=True)
class EnrichResult:
    """Result of enriching a posting with full JD details."""

    jd_text: str
    quality: QualityBand
    enrich_source: str
    error: str | None = None
    posted_at: datetime | None = None


@runtime_checkable
class ScanSession(Protocol):
    """One source session that discovers postings then enriches them.

    Discovery and enrichment share a single session so a source that needs an
    exclusive, expensive resource (e.g. an authenticated browser under a
    cross-process lock) holds it across BOTH phases, not just enrichment.
    """

    async def discover(self, config: dict[str, object]) -> DiscoverResult:
        """Discover postings within the active session.

        Args:
            config: Source-specific configuration.

        Returns:
            Discovery result for the source.
        """
        ...

    async def enrich(self, posting: JobPosting) -> EnrichResult:
        """Enrich a discovered posting within the active session.

        Args:
            posting: Job posting that needs JD enrichment.

        Returns:
            Enrichment result with JD text and quality.
        """
        ...


@runtime_checkable
class SimpleSource(Protocol):
    """Source capability where fetch returns postings with enough JD data."""

    async def fetch_jobs(self, config: dict[str, object]) -> list[JobPosting]:
        """Fetch jobs in one source call.

        Args:
            config: Source-specific configuration.

        Returns:
            Job postings discovered from the source.
        """
        ...


@runtime_checkable
class SessionSource(Protocol):
    """Source capability for a single locked discover-and-enrich session."""

    def session(self) -> AbstractAsyncContextManager[ScanSession]:
        """Open one session covering discovery and enrichment.

        Returns:
            Async context manager yielding a scan session whose lifetime owns
            any exclusive resource (lock, browser context) for both phases.
        """
        ...
