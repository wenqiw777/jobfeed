"""Resolve historical application routes under an existing scan lease."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from jobfeed.domain.models import PipelineRun
from jobfeed.ports.application_resolution import ApplicationIdentityStore
from jobfeed.services.application_resolution import (
    ApplicationResolutionQueue,
    RouteResolver,
)
from jobfeed.services.run_orchestration import RunLeaseSession

_PROGRESS_KEY = "application_resolution"
BackfillProgress = Callable[[PipelineRun], None]


class ApplicationBackfillService:
    """Reuse bounded, cached application evidence resolution for source snapshots."""

    def __init__(
        self, store: ApplicationIdentityStore, resolver: RouteResolver
    ) -> None:
        """Bind persistence and an observed-route resolver.

        Args:
            store: Existing source and atomic application evidence store.
            resolver: Resolver accepting the source Apply URL or original URL.
        """
        self.store = store
        self.resolver = resolver

    async def run(
        self,
        job_ids: list[str],
        *,
        lease_session: RunLeaseSession,
        on_progress: BackfillProgress | None = None,
    ) -> PipelineRun:
        """Drain historical resolution without evaluation or source field writes.

        Args:
            job_ids: Snapshot of source row IDs; repeated IDs count once.
            lease_session: Acquired scan lease; caller owns finalization.
            on_progress: Optional synchronous aggregate progress observer.

        Returns:
            The caller's run with counts for attempted and skipped source rows.

        Raises:
            ValueError: If the supplied lease does not own scan writes.
        """
        if lease_session.kind != "scan":
            raise ValueError("Application backfill requires a scan lease")
        lease_session.ensure_active()
        ids = list(dict.fromkeys(job_ids))
        run = lease_session.run
        run.jobs_discovered = len(ids)
        counts = {
            "resolved": 0,
            "unresolved": 0,
            "ambiguous": 0,
            "blocked": 0,
            "failed": 0,
            "skipped": 0,
        }
        run.scan_stats[_PROGRESS_KEY] = counts
        self._publish(run, "resolving", 0, on_progress)

        def progress(done: int, _total: int, status: str) -> None:
            counts[status] += 1
            if status in {"blocked", "failed"}:
                run.errors += 1
            self._publish(run, "resolving", done, on_progress)

        queue = ApplicationResolutionQueue(
            self.store,
            self.resolver,
            run_id=run.run_id,
            ensure_active=lease_session.ensure_active,
            on_progress=progress,
            lease_fence=(run.run_id, lease_session.owner_id, lease_session.generation),
            allow_missing_apply=True,
        )
        async with queue:
            for job_id in ids:
                await queue.submit_id(job_id)
        counts["skipped"] = len(ids) - queue.total
        self._publish(run, "completed", len(ids), on_progress)
        return run

    @staticmethod
    def _publish(
        run: PipelineRun,
        phase: str,
        done: int,
        callback: BackfillProgress | None,
    ) -> None:
        """Publish one aggregate progress snapshot.

        Args:
            run: Caller-owned historical backfill run.
            phase: Resolving or completed phase.
            done: Committed outcomes plus final skipped rows.
            callback: Optional observer of the updated run.
        """
        now = datetime.now(UTC)
        run.progress_stage = "scan"
        run.scan_source = _PROGRESS_KEY
        run.scan_phase = phase
        run.scan_total = run.jobs_discovered
        run.scan_processed = done
        run.last_progress_at = run.progress_updated_at = now
        run.scan_progress[_PROGRESS_KEY] = {
            "phase": phase,
            "processed": done,
            "done": done,
            "total": run.jobs_discovered,
            **run.scan_stats[_PROGRESS_KEY],
        }
        if callback is not None:
            callback(run)
