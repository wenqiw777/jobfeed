"""Application audit service for recording and reviewing job applications."""

from __future__ import annotations

import difflib
import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

from jobfeed.domain.models import ApplicationRecord, ApplicationStats, ResumeSnapshot
from jobfeed.observability import JobfeedLogger
from jobfeed.ports.store_application import StoreApplicationMixin


@dataclass(kw_only=True, frozen=True)
class ApplyRequest:
    """All inputs for recording a job application."""

    job_id: str
    master_resume: str
    tailored_resume: str | None = None
    cover_letter: str | None = None
    variant: str | None = None
    application_method: str | None = None
    notes: str | None = None
    verdict_snapshot: str | None = None
    fit_snapshot: str | None = None
    hooks_snapshot: str | None = None


@runtime_checkable
class ApplicationStore(StoreApplicationMixin, Protocol):
    """Combined store capability required by ApplicationService."""

    async def compute_reapply_notice(
        self,
        *,
        job_id: str,
        lookback_days: int = 60,
    ) -> str | None:
        """Detect an active application at the same company.

        Args:
            job_id: Job to check (excluded from the search itself).
            lookback_days: How far back to look.

        Returns:
            Notice string if detected, else None.
        """
        ...


class ApplicationService:
    """Orchestrates application audit recording and resume snapshot queries.

    All store and domain dependencies are injected; this service never imports
    concrete adapters or the config module. The CLI resolves file paths and
    passes content directly — this service works with content, not paths.
    """

    def __init__(self, store: ApplicationStore, logger: JobfeedLogger) -> None:
        """Create an application service with injected ports.

        Args:
            store: Persistence port with application audit capabilities.
            logger: Structured logger for application events.
        """
        self._store = store
        self._logger = logger

    async def apply(self, req: ApplyRequest) -> bool:
        """Record a job application with resume snapshots.

        Hashes the resume content, builds snapshot objects, and delegates
        to the store for atomic persistence.

        Args:
            req: Application request containing resume content and metadata.

        Returns:
            True if new application, False if already applied.
        """
        now = datetime.now(UTC)
        master_hash = _content_hash(req.master_resume)
        tailored_hash = (
            _content_hash(req.tailored_resume) if req.tailored_resume else None
        )

        snapshots = _build_snapshots(req, now, master_hash, tailored_hash)

        record = ApplicationRecord(
            job_id=req.job_id,
            applied_at=now,
            master_resume_hash=master_hash,
            tailored_resume_hash=tailored_hash,
            cover_letter=req.cover_letter,
            application_method=req.application_method,
            notes=req.notes,
            verdict_snapshot=req.verdict_snapshot,
            fit_snapshot=req.fit_snapshot,
            hooks_snapshot=req.hooks_snapshot,
        )

        is_new = await self._store.record_application_with_snapshots(
            record,
            snapshots=snapshots,
            resume_variant=req.variant,
        )
        self._logger.info(
            "application_recorded",
            job_id=req.job_id,
            is_new=is_new,
            variant=req.variant,
            has_tailored=req.tailored_resume is not None,
        )
        return is_new

    async def get_application(self, job_id: str) -> ApplicationRecord | None:
        """Load a single application record by job_id.

        Args:
            job_id: Store-assigned job identity.

        Returns:
            Application record if found, else None.
        """
        return await self._store.get_application(job_id)

    async def apply_history(
        self,
        *,
        limit: int = 100,
        resume_hash_prefix: str | None = None,
    ) -> list[ApplicationRecord]:
        """List recent application records.

        Args:
            limit: Maximum number of records to return.
            resume_hash_prefix: Optional literal resume-hash prefix filter.

        Returns:
            Application records ordered by recency.
        """
        return await self._store.list_applications(
            limit=limit,
            resume_hash_prefix=resume_hash_prefix,
        )

    async def reapply_notice(self, job_id: str) -> str | None:
        """Same-company active-application notice for a just-applied job.

        The store method excludes *job_id* itself, so calling this right
        after a successful apply is safe.

        Args:
            job_id: Store-assigned job identity.

        Returns:
            Human-readable notice, or None when no active sibling exists.
        """
        return await self._store.compute_reapply_notice(job_id=job_id)

    async def stats(
        self,
        *,
        since_days_ago: int = 30,
        by_resume: bool = False,
    ) -> ApplicationStats:
        """Aggregate application statistics over a time window.

        Args:
            since_days_ago: Number of days to look back.
            by_resume: Include per-variant breakdown.

        Returns:
            Application statistics.
        """
        return await self._store.application_stats(
            since_days_ago=since_days_ago,
            by_resume=by_resume,
        )

    async def get_snapshot(self, resume_hash: str) -> ResumeSnapshot | None:
        """Retrieve a resume snapshot by its content hash.

        Args:
            resume_hash: SHA-256 content hash.

        Returns:
            Snapshot if found, else None.
        """
        return await self._store.get_resume_snapshot(resume_hash)

    async def diff_snapshots(self, hash_a: str, hash_b: str) -> str:
        """Produce a unified diff between two resume snapshots.

        Args:
            hash_a: SHA-256 hash of the first snapshot.
            hash_b: SHA-256 hash of the second snapshot.

        Returns:
            Unified diff string.

        Raises:
            ValueError: If either snapshot is not found.
        """
        snap_a = await self._store.get_resume_snapshot(hash_a)
        if snap_a is None:
            raise ValueError(f"snapshot not found: {hash_a}")
        snap_b = await self._store.get_resume_snapshot(hash_b)
        if snap_b is None:
            raise ValueError(f"snapshot not found: {hash_b}")
        lines = difflib.unified_diff(
            snap_a.content.splitlines(keepends=True),
            snap_b.content.splitlines(keepends=True),
            fromfile=hash_a,
            tofile=hash_b,
        )
        return "".join(lines)


def _content_hash(text: str) -> str:
    """SHA-256 hex digest of text content."""
    return hashlib.sha256(text.encode()).hexdigest()


def _build_snapshots(
    req: ApplyRequest,
    now: datetime,
    master_hash: str,
    tailored_hash: str | None,
) -> list[ResumeSnapshot]:
    """Build resume snapshot list from an apply request."""
    snapshots = [
        ResumeSnapshot(
            resume_hash=master_hash,
            captured_at=now,
            source="master",
            content=req.master_resume,
        ),
    ]
    if req.tailored_resume is not None and tailored_hash is not None:
        snapshots.append(
            ResumeSnapshot(
                resume_hash=tailored_hash,
                captured_at=now,
                source="tailored",
                content=req.tailored_resume,
            ),
        )
    return snapshots


__all__ = ["ApplicationService", "ApplicationStore", "ApplyRequest"]
