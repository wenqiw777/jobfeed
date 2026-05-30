"""SpeedyApply source: GitHub markdown lists + multi-vendor JD routing.

The speedyapply/2026-SWE-College-Jobs repo publishes daily-updated markdown
tables of SWE intern + new-grad postings. This source fetches each configured
markdown list, parses the rows (``_speedyapply_markdown``), dedupes by
``canonical_id``, then routes each row's apply URL to the matching ATS to fetch
the JD body (``_speedyapply_routing``). It implements ``SimpleSource`` — one
async ``fetch_jobs`` call returns fully-populated postings.

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

# USA internships only by default. The repo also publishes NEW_GRAD_USA.md + the
# two _INTL.md files; users add those to config.search_urls if they want them.
DEFAULT_URL = (
    "https://raw.githubusercontent.com/speedyapply/2026-SWE-College-Jobs/main/README.md"
)

_VENDOR = "speedyapply"


class SpeedyApplySource:
    """Public-facing SpeedyApply source adapter implementing SimpleSource."""

    def __init__(
        self,
        *,
        client: httpx.AsyncClient,
        config: SourcesSpeedyApplyConfig,
        logger: JobfeedLogger,
    ) -> None:
        self._client = client
        self._config = config
        self._log = logger

    async def fetch_jobs(self, config: dict[str, object]) -> list[JobPosting]:  # noqa: ARG002
        """Fetch and JD-enrich every configured speedyapply row.

        Args:
            config: Protocol-satisfying no-op parameter.

        Returns:
            Fully-populated job postings, deduped by canonical_id across lists.
        """
        discovered_at = datetime.now(UTC)
        rows = await self._collect_rows(discovered_at)
        slug_cache: routing.SlugCache = {}
        sem = asyncio.Semaphore(self._config.max_concurrent)
        tasks = [
            self._build_posting(row, slug_cache, sem, discovered_at) for row in rows
        ]
        return await asyncio.gather(*tasks)

    async def _collect_rows(self, discovered_at: datetime) -> list[markdown.SpeedyRow]:
        """Fetch each markdown list, parse rows, dedupe by canonical_id."""
        urls = self._config.search_urls or [DEFAULT_URL]
        parsed: list[markdown.SpeedyRow] = []
        for url in urls:
            parsed.extend(await self._parse_url(url, discovered_at))
        rows = _dedupe_rows(parsed)
        self._log.info("speedyapply_rows_parsed", count=len(rows))
        return rows

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
            jd_text, enrich_source = await self._route(row, slug_cache)
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
            enrich_source=enrich_source,
        )

    async def _route(
        self, row: markdown.SpeedyRow, slug_cache: routing.SlugCache
    ) -> tuple[str, str]:
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
            return ("", "speedyapply-error")


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


__all__ = ["DEFAULT_URL", "SpeedyApplySource"]
