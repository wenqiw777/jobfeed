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
from datetime import UTC, datetime

import httpx

from jobfeed.adapters.sources import _speedyapply_markdown as markdown
from jobfeed.adapters.sources import _speedyapply_routing as routing
from jobfeed.adapters.sources._http import ATSFetchError, fetch_text
from jobfeed.config import SourcesSpeedyApplyConfig
from jobfeed.domain.models import JobPosting
from jobfeed.domain.quality import assess_quality
from jobfeed.observability import JobfeedLogger
from jobfeed.ports.source import ClosedJobLookup

_VENDOR = "speedyapply"
_DEAD_STATUSES = frozenset({404, 410})


class SpeedyApplySource:
    """Public-facing SpeedyApply source adapter implementing SimpleSource."""

    def __init__(
        self,
        *,
        client: httpx.AsyncClient,
        config: SourcesSpeedyApplyConfig,
        logger: JobfeedLogger,
        closed_lookup: ClosedJobLookup | None = None,
    ) -> None:
        self._client = client
        self._config = config
        self._log = logger
        self._closed_lookup = closed_lookup

    async def fetch_jobs(self, config: dict[str, object]) -> list[JobPosting]:  # noqa: ARG002
        """Fetch and JD-enrich every configured speedyapply row.

        Args:
            config: Protocol-satisfying no-op parameter.

        Returns:
            Fully-populated job postings, deduped by canonical_id across lists.
        """
        discovered_at = datetime.now(UTC)
        rows = await self._collect_rows(discovered_at)
        rows = await self._drop_closed(rows)
        slug_cache: routing.SlugCache = {}
        sem = asyncio.Semaphore(self._config.max_concurrent)
        tasks = [
            self._build_posting(row, slug_cache, sem, discovered_at) for row in rows
        ]
        return await asyncio.gather(*tasks)

    async def _collect_rows(self, discovered_at: datetime) -> list[markdown.SpeedyRow]:
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
        so dead links are not re-hit (and re-warned) on every scan. Live rows
        are still re-fetched, so newly-closed postings are detected as before.

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
        """Route + fetch the JD for one row under the concurrency semaphore."""
        async with sem:
            result = await self._route(row, slug_cache)
        jd_text = result.jd_text
        return JobPosting(
            platform=_VENDOR,
            canonical_id=row.canonical_id,
            url=row.apply_url,
            title=row.title,
            company=row.company,
            location=row.location,
            discovered_at=discovered_at,
            jd_text=jd_text or None,
            jd_quality=assess_quality(jd_text),
            posted_at=row.posted_at,
            # Stamp enriched_at only when a JD was actually fetched (routed),
            # matching ATS/JobSpy; unrouted/not-found/error rows stay None so
            # freshness queries don't treat an empty-JD row as enriched.
            enriched_at=discovered_at if jd_text else None,
            enrich_source=result.enrich_source,
            closed_at=result.closed_at,
            enrich_error=result.enrich_error,
        )

    async def _route(
        self, row: markdown.SpeedyRow, slug_cache: routing.SlugCache
    ) -> routing.RouteResult:
        """Route one row's apply URL to its vendor; contain fetch failures."""
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
    return routing.RouteResult(jd_text="", enrich_source="speedyapply-error")


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
