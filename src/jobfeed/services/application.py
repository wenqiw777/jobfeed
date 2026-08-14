"""Application audit service for recording and reviewing job applications."""

from __future__ import annotations

import difflib
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

from jobfeed.domain.models import (
    ApplicationRecord,
    ApplicationStats,
    JobEvaluation,
    ResumeSnapshot,
    ResumeSnapshotSummary,
)
from jobfeed.observability import JobfeedLogger
from jobfeed.ports.store_application import StoreApplicationMixin
from jobfeed.services._application_snapshots import (
    build_snapshots,
    content_hash,
    stage_b_dumps,
)


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

    async def get_evaluation(self, job_id: str) -> JobEvaluation | None:
        """Load a job's evaluation (Stage A/B optional).

        Args:
            job_id: Store-assigned identity.

        Returns:
            Evaluation if the job exists, else None.
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
        master_hash = content_hash(req.master_resume)
        tailored_hash = (
            content_hash(req.tailored_resume) if req.tailored_resume else None
        )

        snapshots = build_snapshots(
            master_resume=req.master_resume,
            tailored_resume=req.tailored_resume,
            now=now,
            master_hash=master_hash,
            tailored_hash=tailored_hash,
        )

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

    async def stage_b_snapshots(
        self, job_id: str
    ) -> tuple[str | None, str | None, str | None]:
        """Capture Stage B verdict/fit/hooks JSON snapshots when available.

        Shared by the CLI apply command and the web apply route so the
        evaluation-derived snapshot fields stay identical across boundaries.

        Args:
            job_id: Store-assigned job identity.

        Returns:
            (verdict, fit_analysis, resume_hooks) JSON strings or Nones.
        """
        evaluation = await self._store.get_evaluation(job_id)
        if evaluation is None or evaluation.stage_b is None:
            return (None, None, None)
        return stage_b_dumps(evaluation.stage_b.raw_blocks or {})

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
        since_days_ago: int | None = 30,
        by_resume: bool = False,
    ) -> ApplicationStats:
        """Aggregate application statistics over a time window.

        Args:
            since_days_ago: Number of days to look back, or None for all time.
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

    async def get_snapshot_by_prefix(self, prefix: str) -> ResumeSnapshot:
        """Resolve a resume snapshot by a unique hash prefix.

        Args:
            prefix: Hash prefix (a full hash is its own prefix).

        Returns:
            The single matching snapshot.

        Raises:
            SnapshotNotFoundError: If no snapshot matches the prefix.
            SnapshotAmbiguousError: If two or more snapshots match.
        """
        return await self._store.get_resume_snapshot_by_prefix(prefix)

    async def list_snapshots(
        self,
        *,
        source: str | None = None,
    ) -> list[ResumeSnapshotSummary]:
        """List every resume snapshot with its usage count.

        Args:
            source: Optional source filter ('master' or 'tailored').

        Returns:
            Snapshot summaries (without content), newest first.
        """
        return await self._store.list_resume_snapshots(source=source)

    async def diff_snapshots(self, prefix_a: str, prefix_b: str) -> str:
        """Produce a unified diff between two prefix-resolved snapshots.

        Args:
            prefix_a: Hash or unique prefix of the first snapshot.
            prefix_b: Hash or unique prefix of the second snapshot.

        Returns:
            Unified diff string (labelled with the full resolved hashes).

        Raises:
            SnapshotNotFoundError: If either prefix matches nothing.
            SnapshotAmbiguousError: If either prefix matches more than one.
        """
        snap_a = await self._store.get_resume_snapshot_by_prefix(prefix_a)
        snap_b = await self._store.get_resume_snapshot_by_prefix(prefix_b)
        lines = difflib.unified_diff(
            snap_a.content.splitlines(keepends=True),
            snap_b.content.splitlines(keepends=True),
            fromfile=snap_a.resume_hash,
            tofile=snap_b.resume_hash,
        )
        return "".join(lines)


__all__ = ["ApplicationService", "ApplicationStore", "ApplyRequest"]
