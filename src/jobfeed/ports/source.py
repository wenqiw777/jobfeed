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


@dataclass(frozen=True, kw_only=True)
class StoredEnrichment:
    """Snapshot of a job's persisted enrichment, used for freshness checks."""

    jd_text: str | None
    quality: QualityBand | None
    enriched_at: datetime | None
    enrich_source: str | None = None


@runtime_checkable
class EnrichmentLookup(Protocol):
    """Read-only probe a session uses to skip re-enriching a fresh stored JD."""

    async def get_enrichment(
        self,
        *,
        platform: str,
        canonical_id: str,
    ) -> StoredEnrichment | None:
        """Return the stored enrichment for a job, or None if absent.

        Args:
            platform: Source platform.
            canonical_id: Platform-specific identity.

        Returns:
            Stored enrichment snapshot, or None when the job is unknown.
        """
        ...


@runtime_checkable
class ClosedJobLookup(Protocol):
    """Read-only probe a source uses to skip re-fetching already-closed jobs.

    A SimpleSource (e.g. SpeedyApply) re-derives its row set from an upstream
    list every scan. Postings the store has already stamped ``closed_at`` (a
    definitively gone 404/410 or an unavailable Workday req) are terminal, so
    re-fetching their JD only burns HTTP and re-logs the same dead-link warning.
    This bulk probe lets the source drop those rows before enrichment.
    """

    async def get_closed_canonical_ids(self, *, platform: str) -> set[str]:
        """Return the canonical ids of definitively closed jobs for a platform.

        Heuristic stale-backfill closures (``mark-stale-closed``) are excluded
        so the save-path self-heal can recover them: clearing their
        ``closed_at`` requires a later JD fetch to succeed, which means the
        source must keep re-fetching them.

        Args:
            platform: Source platform to scope the lookup to.

        Returns:
            The set of ``canonical_id`` values whose row is definitively
            closed. Empty when none are closed (or the platform is unknown).
        """
        ...


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
