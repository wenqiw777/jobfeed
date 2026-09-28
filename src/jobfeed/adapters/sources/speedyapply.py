"""SpeedyApply source: configured GitHub markdown lists + JD routing.

This source fetches each configured markdown list, parses the rows
(``_speedyapply_markdown``), dedupes by ``canonical_id``, then routes each
row's apply URL to the matching ATS to fetch the JD body
(``_speedyapply_routing``). It implements ``SimpleSource`` — one async
``fetch_jobs`` call returns fully-populated postings.

A single per-call slug cache is shared across rows so multiple postings from the
same Ashby/Lever board fetch the board once. Per-row fetch errors are contained:
the row is still returned with an empty JD so one slow/degraded vendor cannot
stall or abort the batch.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any, cast
from urllib.parse import urlparse

import httpx
from pydantic import TypeAdapter

from jobfeed.adapters.sources import _speedyapply_markdown as markdown
from jobfeed.adapters.sources import _speedyapply_routing as routing
from jobfeed.adapters.sources._http import ATSFetchError, fetch_text
from jobfeed.adapters.sources._speedyapply_observed import enrich_observed
from jobfeed.config import SourcesSpeedyApplyConfig
from jobfeed.domain.enrichment_retry import retry_policy
from jobfeed.domain.external_identity import external_identity
from jobfeed.domain.models import JobPosting, QualityBand
from jobfeed.domain.quality import assess_quality, quality_rank
from jobfeed.observability import JobfeedLogger
from jobfeed.ports.source import (
    ClosedJobLookup,
    EnrichmentLookup,
    PartialSourceFetchError,
    SourceFetchProgress,
    SourceFetchProgressCallback,
    StoredEnrichment,
)
from jobfeed.services.job_page_extraction import JobPageExtractor
from jobfeed.services.jobright_bridge import JobrightBridge, JobrightBridgeError
from jobfeed.services.pipeline_context import current_pipeline, durable_posting

_VENDOR = "speedyapply"
_DEAD_STATUSES = frozenset({404, 410})


class SpeedyApplySource:
    """Public-facing SpeedyApply source adapter implementing SimpleSource."""

    def __init__(  # noqa: PLR0913 - independent optional source capabilities
        self,
        *,
        client: httpx.AsyncClient,
        config: SourcesSpeedyApplyConfig,
        logger: JobfeedLogger,
        closed_lookup: ClosedJobLookup | None = None,
        enrichment_lookup: EnrichmentLookup | None = None,
        bridge: JobrightBridge | None = None,
        page_extractor: JobPageExtractor | None = None,
    ) -> None:
        self._client = client
        self._config = config
        self._log = logger
        self._closed_lookup = closed_lookup
        self._enrichment_lookup = enrichment_lookup
        self._bridge = bridge
        self._page_extractor = page_extractor
        self._on_progress: SourceFetchProgressCallback | None = None
        self.stats: dict[str, int] = {}

    async def fetch_jobs_with_progress(
        self, config: dict[str, object], on_progress: SourceFetchProgressCallback
    ) -> list[JobPosting]:
        """Report list, native, and browser work independently of other sources.

        Args:
            config: Source-specific request options.
            on_progress: Callback receiving scan progress updates.

        Returns:
            Collected GitHub feed postings.
        """
        self._on_progress = on_progress
        try:
            return await self.fetch_jobs(config)
        finally:
            self._on_progress = None

    def _progress(self, phase: str, processed: int, total: int) -> None:
        if self._on_progress:
            self._on_progress(
                SourceFetchProgress(processed=processed, total=total, phase=phase)
            )

    async def fetch_jobs(self, config: dict[str, object]) -> list[JobPosting]:  # noqa: ARG002
        """Fetch and JD-enrich every configured speedyapply row.

        Args:
            config: Protocol-satisfying no-op parameter.

        Returns:
            Fully-populated job postings, deduped by canonical_id across lists.

        Raises:
            PartialSourceFetchError: If enrichment fails after collecting postings.
        """
        discovered_at = datetime.now(UTC)
        self.stats = dict.fromkeys(
            (
                "reused",
                "native_enriched",
                "browser_enriched",
                "retry_deferred",
                "deduped_targets",
                "failed",
            ),
            0,
        )
        rows = await self._collect_rows(discovered_at)
        rows = await self._drop_closed(rows)
        rows = rows[: self._config.max_jobs]
        slug_cache: routing.SlugCache = {}
        sem = asyncio.Semaphore(self._config.max_concurrent)
        self._progress("native_enrichment", 0, len(rows))
        groups: dict[str, list[markdown.SpeedyRow]] = {}
        for row in rows:
            groups.setdefault(
                external_identity(row.apply_url) or row.canonical_id, []
            ).append(row)
        self.stats["deduped_targets"] = len(rows) - len(groups)
        processed = 0

        async def build(group: list[markdown.SpeedyRow]) -> list[JobPosting]:
            nonlocal processed
            job = await self._build_posting(group[0], slug_cache, sem, discovered_at)
            processed += len(group)
            self._progress("native_enrichment", processed, len(rows))
            return [
                replace(
                    job,
                    canonical_id=row.canonical_id,
                    url=row.apply_url,
                    title=row.title,
                    company=row.company,
                    location=row.location,
                    posted_at=row.posted_at,
                )
                for row in group
            ]

        batches = await asyncio.gather(*(build(group) for group in groups.values()))
        postings = [job for batch in batches for job in batch]
        results, error = await self._fetch_missing_descriptions(postings)
        updated = []
        for job in postings:
            replacement = job
            result = results.get(job.canonical_id)
            if (
                result
                and isinstance(result.get("description"), str)
                and result["description"].strip()
            ):
                body = result["description"]
                quality = _browser_quality(result)
                if quality_rank(quality) >= quality_rank(job.jd_quality):
                    replacement = replace(
                        job,
                        jd_text=body,
                        jd_quality=quality,
                        enriched_at=datetime.now(UTC)
                        if quality in {QualityBand.GOOD, QualityBand.FULL}
                        else job.enriched_at,
                        enrich_source="github_extension",
                        enrich_error=None,
                    )
            elif result:
                replacement = replace(
                    job, enrich_error=str(result.get("error") or "No complete JD")
                )
            if result or (
                self._bridge is None
                and job.closed_at is None
                and job.enrich_retry_after is None
                and job.jd_quality not in {QualityBand.GOOD, QualityBand.FULL}
            ):
                now = datetime.now(UTC)
                if (
                    replacement.jd_quality in {QualityBand.GOOD, QualityBand.FULL}
                    and (replacement.jd_text or "").strip()
                ):
                    replacement = replace(
                        replacement,
                        enrich_attempted_at=now,
                        enrich_error_code=None,
                        enrich_retry_after=None,
                    )
                    self.stats["browser_enriched"] += 1
                else:
                    detail = replacement.enrich_error or "No complete JD"
                    code, retry_at = retry_policy(
                        detail, now, code=result.get("error_code") if result else None
                    )
                    replacement = replace(
                        replacement,
                        enrich_attempted_at=now,
                        enrich_error=detail,
                        enrich_error_code=code,
                        enrich_retry_after=retry_at,
                    )
                    self.stats["failed"] += 1
            await self._record_browser_attempt(job, result)
            updated.append(replacement)
        if error:
            raise PartialSourceFetchError(error, updated, warning=self._item_warning)
        return updated

    async def _record_browser_attempt(
        self, job: JobPosting, result: dict[str, Any] | None
    ) -> None:
        if result and self._page_extractor is not None:
            await self._page_extractor.record_retry_upgrade(
                job.external_identity or job.canonical_id
            )

    async def _defer_browser_retry(self, job: JobPosting, now: datetime) -> bool:
        if not _retry_deferred(job, now):
            return False
        upgrade = (
            self._page_extractor is not None
            and job.enrich_error_code not in {"auth_required", "rate_limited"}
            and "maintenance" not in (job.enrich_error or "").lower()
            and await self._page_extractor.needs_retry_upgrade(
                job.external_identity or job.canonical_id
            )
        )
        return not upgrade

    async def _fetch_missing_descriptions(
        self, postings: list[JobPosting]
    ) -> tuple[dict[str, dict[str, Any]], str | None]:
        """Time complexity: O(J + R), over job targets J and returned browser rows R."""
        self._item_warning = False
        groups: dict[str, list[JobPosting]] = {}
        now = datetime.now(UTC)
        for job in postings:
            if (
                job.jd_quality in {QualityBand.GOOD, QualityBand.FULL}
                and (job.jd_text or "").strip()
            ) or job.closed_at is not None:
                continue
            if await self._defer_browser_retry(job, now):
                continue
            groups.setdefault(job.external_identity or job.canonical_id, []).append(job)
        targets = [
            {
                "id": jobs[0].canonical_id,
                "url": jobs[0].url,
                "title": jobs[0].title,
                "company": jobs[0].company,
            }
            for jobs in groups.values()
        ]
        if not targets or self._bridge is None:
            return {}, None
        error = None
        self._progress("browser_enrichment", 0, len(targets))
        try:
            rows = await self._bridge.run_scan(
                source="github-jd",
                targets=targets,
                max_jobs=len(targets),
                batch_size=24,
                pacing_s=0,
                timeout_s=3600,
                on_progress=lambda update: self._progress(
                    "browser_enrichment", update.processed, len(targets)
                ),
            )
        except JobrightBridgeError as exc:
            rows, error = exc.partial_jobs, str(exc)
        results = await self._interpret_browser_rows(targets, rows)
        for target in targets:
            results.setdefault(
                target["id"], {"error": error or "Extension returned no result"}
            )
        complete = sum(
            isinstance(row.get("description"), str)
            and bool(row["description"].strip())
            and (
                row.get("extraction_status") == "complete"
                or assess_quality(row["description"])
                in {QualityBand.GOOD, QualityBand.FULL}
            )
            for row in results.values()
        )
        if complete != len(targets) and error is None:
            self._item_warning = complete > 0
            error = (
                f"GitHub extension returned {complete}/{len(targets)} "
                "complete descriptions"
            )
        for jobs in groups.values():
            for job in jobs[1:]:
                results[job.canonical_id] = results[jobs[0].canonical_id]
        return results, error

    async def _interpret_browser_rows(
        self, targets: list[dict[str, Any]], rows: list[dict[str, Any]]
    ) -> dict[str, dict[str, Any]]:
        expected = {target["id"]: target["url"] for target in targets}
        results = {
            str(row["id"]): row
            for row in rows
            if expected.get(str(row.get("id"))) == row.get("url")
        }
        await enrich_observed(self._client, targets, results)
        if self._page_extractor is not None:
            await self._page_extractor.enrich_targets(targets, results)
        return results

    async def _collect_rows(self, discovered_at: datetime) -> list[markdown.SpeedyRow]:
        pipeline = current_pipeline.get()
        if pipeline is None:
            return await self._collect_live_rows(discovered_at)
        codec = TypeAdapter(list[markdown.SpeedyRow])

        async def collect(_payload: Any) -> Any:
            return codec.dump_python(
                await self._collect_live_rows(discovered_at), mode="json"
            )

        return codec.validate_python(
            await pipeline.step("discovery:speedyapply", {}, collect)
        )

    async def _collect_live_rows(
        self, discovered_at: datetime
    ) -> list[markdown.SpeedyRow]:
        """Fetch each markdown list, parse rows, dedupe by canonical_id."""
        parsed: list[markdown.SpeedyRow] = []
        for url in self._config.search_urls:
            parsed.extend(await self._parse_url(url, discovered_at))
        rows = _dedupe_rows(parsed)
        self._log.info("speedyapply_rows_parsed", count=len(rows))
        return rows

    async def _drop_closed(
        self, rows: list[markdown.SpeedyRow]
    ) -> list[markdown.SpeedyRow]:
        """Drop rows whose canonical_id the store already stamped closed.

        Skips the JD fetch for definitively-gone postings (404/410/unavailable)
        so dead links are not re-hit (and re-warned) on every scan. Complete
        stored JDs are reused separately; their live status is not re-probed.

        The filter is only an optimization: a transient lookup error fails
        open (warn + return rows unfiltered) rather than abort the scan round.
        """
        if self._closed_lookup is None:
            return rows
        try:
            closed = await self._closed_lookup.get_closed_canonical_ids(
                platform=_VENDOR
            )
        except Exception as exc:
            self._log.warning("speedyapply_closed_lookup_failed", error=str(exc))
            return rows
        if not closed:
            return rows
        kept = [row for row in rows if row.canonical_id not in closed]
        skipped = len(rows) - len(kept)
        if skipped:
            self._log.info("speedyapply_dead_skipped", count=skipped)
        return kept

    async def _parse_url(self, url: str, now: datetime) -> list[markdown.SpeedyRow]:
        """Fetch one markdown list and parse it; contain per-URL fetch errors."""
        try:
            text = await fetch_text(
                self._client,
                url,
                slug=_VENDOR,
                vendor=_VENDOR,
                timeout=self._config.fetch_timeout_s,
            )
        except ATSFetchError as exc:
            self._log.warning("speedyapply_list_fetch_failed", url=url, error=str(exc))
            return []
        return markdown.parse_rows(text, now=now)

    async def _build_posting(
        self,
        row: markdown.SpeedyRow,
        slug_cache: routing.SlugCache,
        sem: asyncio.Semaphore,
        discovered_at: datetime,
    ) -> JobPosting:
        if current_pipeline.get() is None:
            return await self._build_live_posting(row, slug_cache, sem, discovered_at)
        codec = TypeAdapter(markdown.SpeedyRow)

        async def enrich(payload: Any) -> JobPosting:
            return await self._build_live_posting(
                codec.validate_python(payload), slug_cache, sem, discovered_at
            )

        result = await durable_posting(
            f"native:speedyapply:{row.canonical_id}",
            codec.dump_python(row, mode="json"),
            enrich,
        )
        assert result is not None
        return result

    async def _build_live_posting(
        self,
        row: markdown.SpeedyRow,
        slug_cache: routing.SlugCache,
        sem: asyncio.Semaphore,
        discovered_at: datetime,
    ) -> JobPosting:
        """Reuse a complete stored JD, otherwise route under the semaphore."""
        async with sem:
            stored = await self._reusable_enrichment(
                row.canonical_id, external_identity(row.apply_url)
            )
            result = (
                routing.RouteResult(
                    jd_text=stored.jd_text or "",
                    enrich_source=stored.enrich_source or "",
                )
                if stored is not None
                else await self._route(row, slug_cache)
            )
        jd_text = result.jd_text
        complete = bool(
            stored
            and stored.quality in {QualityBand.GOOD, QualityBand.FULL}
            and (stored.jd_text or "").strip()
        )
        if stored:
            self.stats["reused" if complete else "retry_deferred"] += 1
        elif assess_quality(jd_text) in {QualityBand.GOOD, QualityBand.FULL}:
            self.stats["native_enriched"] += 1
        return JobPosting(
            platform=_VENDOR,
            canonical_id=row.canonical_id,
            url=row.apply_url,
            title=row.title,
            company=row.company,
            location=row.location,
            discovered_at=discovered_at,
            jd_text=jd_text or None,
            jd_quality=stored.quality if stored else assess_quality(jd_text),
            posted_at=row.posted_at,
            # Stamp enriched_at only when a JD was actually fetched (routed),
            # matching ATS/JobSpy; unrouted/not-found/error rows stay None so
            # freshness queries don't treat an empty-JD row as enriched.
            enriched_at=(
                stored.enriched_at if stored else discovered_at if jd_text else None
            ),
            enrich_source=stored.enrich_source if stored else result.enrich_source,
            closed_at=result.closed_at,
            enrich_error=stored.enrich_error
            if stored and not complete
            else result.enrich_error,
            external_identity=(stored.external_identity if stored else None)
            or external_identity(row.apply_url),
            enrich_attempted_at=stored.enrich_attempted_at if stored else discovered_at,
            enrich_error_code=stored.enrich_error_code
            if stored and not complete
            else None,
            enrich_retry_after=stored.enrich_retry_after
            if stored and not complete
            else None,
        )

    async def _reusable_enrichment(
        self, canonical_id: str, identity: str | None = None
    ) -> StoredEnrichment | None:
        """Reuse complete bodies; a failed lookup must not launch blind requests."""
        if self._enrichment_lookup is None:
            return None
        try:
            stored = await self._enrichment_lookup.get_enrichment(
                platform=_VENDOR, canonical_id=canonical_id
            )
            if (
                stored
                and identity
                and stored.external_identity
                and stored.external_identity != identity
            ):
                stored = None
            if (
                stored
                and stored.quality in {QualityBand.GOOD, QualityBand.FULL}
                and (stored.jd_text or "").strip()
            ):
                return stored
            lookup = getattr(
                self._enrichment_lookup, "get_enrichment_by_identity", None
            )
            if identity and lookup:
                twin = cast(StoredEnrichment | None, await lookup(identity))
                if (
                    twin
                    and twin.quality in {QualityBand.GOOD, QualityBand.FULL}
                    and (twin.jd_text or "").strip()
                ):
                    return replace(
                        twin,
                        enrich_source=(
                            f"reused:{twin.platform}:{twin.enrich_source or 'stored'}"
                        ),
                    )
                if twin and twin.enrich_retry_after:
                    stored = twin
            if (
                stored
                and stored.enrich_retry_after
                and stored.enrich_retry_after > datetime.now(UTC)
            ):
                return stored
        except Exception as exc:
            self._log.warning("speedyapply_enrichment_lookup_failed", error=str(exc))
            raise
        if (
            stored is not None
            and stored.quality in {QualityBand.GOOD, QualityBand.FULL}
            and (stored.jd_text or "").strip()
        ):
            return stored
        return None

    async def _route(
        self, row: markdown.SpeedyRow, slug_cache: routing.SlugCache
    ) -> routing.RouteResult:
        """Route one row's apply URL to its vendor; contain fetch failures."""
        if self._bridge is not None and urlparse(row.apply_url).hostname in {
            "jobright.ai",
            "www.jobright.ai",
            "lifeattiktok.com",
        }:
            return routing.RouteResult(jd_text="", enrich_source="extension-pending")
        try:
            return await routing.route_and_fetch(
                self._client,
                row.apply_url,
                slug_cache=slug_cache,
                timeout=self._config.fetch_timeout_s,
            )
        except ATSFetchError as exc:
            self._log.warning(
                "speedyapply_jd_fetch_failed", url=row.apply_url, error=str(exc)
            )
            return _closed_route_result(exc)


def _closed_route_result(exc: ATSFetchError) -> routing.RouteResult:
    """Map an ATSFetchError to a RouteResult, setting closed_at for 404/410.

    Args:
        exc: The ATSFetchError raised during vendor JD fetch.

    Returns:
        RouteResult with ``closed_at`` and ``enrich_error`` populated for
        definitive HTTP-gone errors (404/410); plain error result otherwise.
    """
    if exc.status_code in _DEAD_STATUSES:
        return routing.RouteResult(
            jd_text="",
            enrich_source="speedyapply-error",
            closed_at=datetime.now(UTC),
            enrich_error=f"gone:{exc.status_code}:{exc.vendor}",
        )
    return routing.RouteResult(
        jd_text="", enrich_source="speedyapply-error", enrich_error=str(exc)
    )


def _dedupe_rows(rows: list[markdown.SpeedyRow]) -> list[markdown.SpeedyRow]:
    """Drop rows whose canonical_id was already seen, keeping document order.

    The same canonical_id can appear across multiple tables/lists (e.g. the
    FAANG+ vs Other split inside one file); the first occurrence wins.

    Args:
        rows: Parsed rows across all configured lists, in document order.

    Returns:
        Deduped rows, first-occurrence order preserved. Time complexity O(N)
        over the N input rows (single pass, set membership).
    """
    seen: set[str] = set()
    deduped: list[markdown.SpeedyRow] = []
    for row in rows:
        if row.canonical_id in seen:
            continue
        seen.add(row.canonical_id)
        deduped.append(row)
    return deduped


__all__ = ["SpeedyApplySource"]


def _retry_deferred(job: JobPosting, now: datetime) -> bool:
    permission_blocked = (
        job.enrich_error_code == "missing_permission"
        or "permission" in (job.enrich_error or "").lower()
    )
    return bool(
        job.enrich_retry_after
        and job.enrich_retry_after > now
        and not permission_blocked
    )


def _browser_quality(result: dict[str, Any]) -> QualityBand:
    if result.get("extraction_status") == "complete":
        return QualityBand.FULL
    return assess_quality(result["description"])
