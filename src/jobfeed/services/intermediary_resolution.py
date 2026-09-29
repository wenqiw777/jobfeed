"""Internal-first attribution, bounded external search and durable dispositions."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from jobfeed.domain.errors import RunLeaseLostError
from jobfeed.domain.intermediary import intermediary_posting, matches_official
from jobfeed.domain.models import JobPosting
from jobfeed.ports.intermediary import IntermediaryStore

OfficialSearch = Callable[[JobPosting, str], Awaitable[list[JobPosting]]]
_MAX_CANDIDATES = 200


class IntermediaryResolver:
    """Keep unresolved publishers out of evaluation, without deleting evidence."""

    def __init__(
        self,
        store: IntermediaryStore,
        *,
        search: OfficialSearch | None = None,
        max_searches: int = 3,
        timeout_s: float = 60,
    ) -> None:
        self.store = store
        self.search = search
        self.max_searches = max_searches
        self.timeout_s = timeout_s

    async def resolve(
        self,
        jobs: list[JobPosting],
        *,
        run_id: str,
        ensure_active: Callable[[], None] = lambda: None,
        on_progress: Callable[[int, int], None] = lambda _done, _total: None,
    ) -> None:
        """Resolve this scan's intermediaries after all source writes finish.

        Args:
            jobs: Observed source postings; duplicates are collapsed by native ID.
            run_id: Owning scan, included in persistent attribution evidence.
        """
        searches = 0
        unique = {
            (j.platform, j.canonical_id): j for j in jobs if intermediary_posting(j)
        }
        for index, incoming in enumerate(unique.values()):
            ensure_active()
            on_progress(index, len(unique))
            saved = await self.store.save_job(incoming)
            stored = await self.store.get_job(saved.job_id)
            source = stored or incoming
            key = f"intermediary-resolution:{source.platform}:{source.canonical_id}"
            candidates = await self.store.official_candidates(source.title)
            if len(candidates) > _MAX_CANDIDATES:
                await self._record(key, run_id, "candidate_overflow")
                continue
            matches = {
                parent: job
                for parent, job in candidates
                if matches_official(source, job)
            }
            if len(matches) > 1:
                await self._record(key, run_id, "ambiguous_internal")
                continue
            if matches:
                target = next(iter(matches.values()))
                await self._link(source, target, key, run_id, "matched_internal")
                continue
            previous = await self.store.get_state(key)
            if previous:
                evidence = json.loads(previous)
                if datetime.fromisoformat(evidence["retry_after"]) > datetime.now(UTC):
                    continue
            if self.search is None or searches >= self.max_searches:
                await self._record(key, run_id, "search_deferred", retry_hours=0)
                continue
            searches += 1
            await self._external(source, run_id, key, ensure_active)

    async def _external(
        self,
        source: JobPosting,
        run_id: str,
        key: str,
        ensure_active: Callable[[], None],
    ) -> None:
        assert self.search is not None
        try:
            async with asyncio.timeout(self.timeout_s):
                found = await self.search(source, run_id)
        except RunLeaseLostError:
            raise
        except Exception as exc:
            ensure_active()
            await self._record(
                key, run_id, "search_failed", error=str(exc), retry_hours=24
            )
            return
        ensure_active()
        verified = {j.canonical_id: j for j in found if matches_official(source, j)}
        if len(verified) == 1:
            target = next(iter(verified.values()))
            saved = await self.store.save_job(target)
            target = replace(target, id=saved.job_id)
            await self._link(source, target, key, run_id, "matched_external")
        else:
            await self._record(
                key,
                run_id,
                "ambiguous_external" if verified else "no_verified_official",
            )

    async def _link(
        self,
        source: JobPosting,
        target: JobPosting,
        key: str,
        run_id: str,
        outcome: str,
    ) -> None:
        # Retain original source URL/JD. Existing scoped ATS identity resolution
        # associates the source; canonical selection uses the official JD.
        linked = await self.store.save_job(
            replace(source, identity_evidence_url=target.url)
        )
        assert target.id is not None
        parents = await self.store.resolve_real_job_ids([linked.job_id, target.id])
        actual = outcome if len(parents) == 1 else "identity_conflict"
        await self._record(key, run_id, actual, official_url=target.url)

    async def _record(  # noqa: PLR0913 - durable disposition metadata
        self,
        key: str,
        run_id: str,
        outcome: str,
        *,
        official_url: str | None = None,
        error: str | None = None,
        retry_hours: int = 168,
    ) -> None:
        now = datetime.now(UTC)
        await self.store.set_state(
            key,
            json.dumps(
                {
                    "run_id": run_id,
                    "outcome": outcome,
                    "official_url": official_url,
                    "error": error,
                    "updated_at": now.isoformat(),
                    "retry_after": (now + timedelta(hours=retry_hours)).isoformat(),
                }
            ),
        )
