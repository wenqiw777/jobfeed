"""Run-scoped, bounded application-route work overlapping committed scan batches."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from typing import Any

from jobfeed.domain.application_route import (
    ApplicationRouteHop,
    ApplicationRouteOutcome,
)
from jobfeed.domain.errors import RunLeaseLostError
from jobfeed.domain.models import JobPosting
from jobfeed.domain.normalize import normalize, normalize_company
from jobfeed.domain.real_job_identity import normalized_jd_body
from jobfeed.ports.application_resolution import ApplicationIdentityStore

RouteResolver = Callable[[JobPosting], Awaitable[ApplicationRouteOutcome]]
ResolutionProgress = Callable[[int, int, str], None]


class ApplicationResolutionQueue:
    """Fetch concurrently, then commit against current globally scoped identities."""

    def __init__(  # noqa: PLR0913 - explicit lifecycle dependencies
        self,
        store: ApplicationIdentityStore,
        resolver: RouteResolver,
        *,
        run_id: str = "",
        ensure_active: Callable[[], None] = lambda: None,
        writer_lock: asyncio.Lock | None = None,
        on_progress: ResolutionProgress = lambda _done, _total, _status: None,
        queue_size: int = 50,
        lease_fence: tuple[str, str, int] | None = None,
        allow_missing_apply: bool = False,
    ) -> None:
        self.store, self.resolver, self.run_id = store, resolver, run_id
        self.ensure_active, self.on_progress = ensure_active, on_progress
        self.writer_lock = writer_lock or asyncio.Lock()
        self.lease_fence = lease_fence
        self.allow_missing_apply = allow_missing_apply
        self.queue: asyncio.Queue[JobPosting] = asyncio.Queue(maxsize=queue_size)
        self._workers: list[asyncio.Task[None]] = []
        self._routes: dict[tuple[str, str], asyncio.Task[ApplicationRouteOutcome]] = {}
        self._submitted: set[tuple[str, str]] = set()
        self._failure: asyncio.Future[BaseException] | None = None
        self.total = self.completed = 0

    async def __aenter__(self) -> ApplicationResolutionQueue:
        """Start three workers owned by the current scan/enrichment lifetime."""
        self._failure = asyncio.get_running_loop().create_future()
        self._workers = [
            asyncio.create_task(self._worker(), name=f"application-resolution:{index}")
            for index in range(3)
        ]
        return self

    async def __aexit__(self, kind: Any, error: Any, traceback: Any) -> None:
        """Drain on success; cancel all owned work on failure or interruption."""
        try:
            if kind is None:
                await self.drain()
        finally:
            tasks = [*self._workers, *self._routes.values()]
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def submit_id(self, job_id: str) -> None:
        """Enqueue an existing source ID after its durable source save finishes.

        Args:
            job_id: Source row ID whose save has committed.
        """
        self.ensure_active()
        self._raise_failure()
        job = await self.store.get_job(job_id)
        if job is None or (not job.apply_url and not self.allow_missing_apply):
            return
        key = (job_id, job.apply_url or job.url)
        if key in self._submitted:
            return
        self._submitted.add(key)
        self.total += 1
        assert self._failure is not None
        put = asyncio.create_task(self.queue.put(job))
        try:
            waiters: list[asyncio.Future[Any]] = [put, self._failure]
            await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
            self._raise_failure()
            await put
        finally:
            if not put.done():
                put.cancel()
                await asyncio.gather(put, return_exceptions=True)

    async def drain(self) -> None:
        """Wait for all evidence commits before canonical evaluation can begin."""
        self._raise_failure()
        assert self._failure is not None
        joined = asyncio.create_task(self.queue.join())
        try:
            waiters: list[asyncio.Future[Any]] = [joined, self._failure]
            await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
            self._raise_failure()
            await joined
            self.ensure_active()
        finally:
            if not joined.done():
                joined.cancel()
                await asyncio.gather(joined, return_exceptions=True)

    def _raise_failure(self) -> None:
        if self._failure is not None and self._failure.done():
            raise self._failure.result()

    async def _worker(self) -> None:
        while True:
            job = await self.queue.get()
            try:
                self.ensure_active()
                outcome = await self._process(job)
                self.completed += 1
                self.on_progress(self.completed, self.total, outcome.status)
            except asyncio.CancelledError:
                raise
            except BaseException as exc:
                assert self._failure is not None
                if not self._failure.done():
                    self._failure.set_result(exc)
                return
            finally:
                self.queue.task_done()

    async def _process(self, job: JobPosting) -> ApplicationRouteOutcome:
        for _attempt in range(2):
            outcome = await self._outcome(job)
            self.ensure_active()
            if await self._commit(job, outcome):
                return outcome
            current = await self.store.get_job(job.id or "")
            if current is None or current.apply_url != job.apply_url:
                break
            job = current
        return ApplicationRouteOutcome(
            status="unresolved", reason="source_changed_during_resolution"
        )

    async def _outcome(self, job: JobPosting) -> ApplicationRouteOutcome:
        cached = await self.store.get_state(self._state_key(job))
        if cached:
            reused = self._cached(cached, job)
            if reused is not None:
                return reused
        key = (job.apply_url or job.url, json.dumps(self._facts(job), sort_keys=True))
        if key not in self._routes:
            self._routes[key] = asyncio.create_task(self._resolve(job))
        return await asyncio.shield(self._routes[key])

    async def _resolve(self, job: JobPosting) -> ApplicationRouteOutcome:
        try:
            return await self.resolver(job)
        except RunLeaseLostError:
            raise
        except Exception as exc:
            return ApplicationRouteOutcome(
                status="failed", reason=f"{type(exc).__name__}: {exc}"
            )

    async def _commit(self, job: JobPosting, outcome: ApplicationRouteOutcome) -> bool:
        assert job.id is not None
        evidence = {
            **asdict(outcome),
            "checked_at": outcome.checked_at.isoformat(),
            "run_id": self.run_id,
            "apply_url": job.apply_url,
            "source_url": job.url,
            "company": job.company,
            "title": job.title,
            "verification_facts": self._facts(job),
        }
        async with self.writer_lock:
            self.ensure_active()
            return await self.store.record_application_identity(
                job_id=job.id,
                expected_apply_url=job.apply_url,
                ats_url=outcome.ats_url if outcome.status == "resolved" else None,
                state_key=self._state_key(job),
                state_value=json.dumps(evidence),
                run_id=self.lease_fence[0] if self.lease_fence else None,
                owner_id=self.lease_fence[1] if self.lease_fence else None,
                generation=self.lease_fence[2] if self.lease_fence else None,
            )

    @staticmethod
    def _facts(job: JobPosting) -> dict[str, str]:
        return {
            "company": normalize_company(job.company),
            "title": normalize(job.title),
            "jd_body": normalized_jd_body(job.jd_text),
        }

    @staticmethod
    def _state_key(job: JobPosting) -> str:
        return f"application-resolution:{job.platform}:{job.canonical_id}"

    @staticmethod
    def _cached(value: str, job: JobPosting) -> ApplicationRouteOutcome | None:
        try:
            saved = json.loads(value)
            if (
                saved["apply_url"] != job.apply_url
                or (not job.apply_url and saved.get("source_url") != job.url)
                or saved.get("company") != job.company
                or saved.get("title") != job.title
                or saved.get("verification_facts")
                != ApplicationResolutionQueue._facts(job)
            ):
                return None
            checked = datetime.fromisoformat(saved["checked_at"])
            lifetime = timedelta(days=7 if saved["status"] == "resolved" else 1)
            if checked + lifetime <= datetime.now(UTC):
                return None
            return ApplicationRouteOutcome(
                status=saved["status"],
                ats_url=saved.get("ats_url"),
                reason=saved.get("reason", ""),
                checked_at=checked,
                hops=tuple(ApplicationRouteHop(**hop) for hop in saved.get("hops", [])),
            )
        except (ValueError, KeyError, TypeError):
            return None
