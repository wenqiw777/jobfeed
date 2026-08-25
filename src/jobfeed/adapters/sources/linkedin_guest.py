"""LinkedIn source scraping the public guest endpoints directly (SimpleSource).

Discovery hits the same anonymous ``jobs-guest`` list endpoint JobSpy uses,
but owned in-repo: pagination is correct (``start`` advances by the page's
RAW card count — parse-skipped cards included, never the accumulated unique
total, the JobSpy bug this
replaces), failures are contained per search URL (a 429/999/empty page ends
that URL and keeps what was already collected), and the HTTP fetcher plus the
pacing sleep are injected so tests never touch the network. The per-run
internals live in ``_linkedin_guest_discover``.

Postings come from the LIST endpoint only: ``jd_text=None`` /
``enriched_at=None``. JD bodies are filled in by a later paced enrich pass
against the guest posting endpoint — both endpoints share LinkedIn's per-IP
budget, which is why every page fetch after the first sleeps ``pacing_s``.
``LinkedInGuestEnricher`` (the ``JobEnricher`` port) is that pass's
per-posting fetch: one guest ``jobPosting/{bare_id}`` GET, classified into a
usable JD, a rate-limit block (429/999), a definitively gone posting
(404/410), or an error. Pacing and backoff belong to the enrich service, not
the enricher.

The platform tag is ``linkedin_guest`` (distinct from the authenticated
Playwright source's ``linkedin``) so guest rows never collide with that
lineage.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from jobfeed.adapters.sources._linkedin_guest_discover import _GuestRun
from jobfeed.adapters.sources._linkedin_guest_http import (
    GuestResponse,
    create_client,
    fetch,
    posting_url,
)
from jobfeed.adapters.sources._linkedin_guest_parse import (
    parse_jd,
    parse_posting_posted_at,
)
from jobfeed.domain.models import JobPosting
from jobfeed.domain.quality import assess_quality
from jobfeed.observability import JobfeedLogger
from jobfeed.ports.enrich import EnrichOutcome
from jobfeed.ports.source import EnrichResult

# A guest posting page whose JD parses to this many chars or fewer is treated
# as unusable (authwall teaser / stripped body), not a successful enrichment.
MIN_JD_CHARS = 200

_ENRICH_SOURCE = "linkedin_guest"
_HTTP_OK = 200
_BLOCKED_STATUSES = frozenset({429, 999})
_GONE_STATUSES = frozenset({404, 410})
# Bare-id derivation: an optional non-digit prefix (legacy "li-" forms)
# followed by the numeric id; anything else is not a guest job id.
_BARE_ID_RE = re.compile(r"\D*(\d+)")

Fetcher = Callable[[str], Awaitable[GuestResponse]]
AsyncSleeper = Callable[[float], Awaitable[None]]


@dataclass(frozen=True, kw_only=True)
class GuestSourceSettings:
    """Static settings for the guest source (config maps onto this)."""

    search_urls: Sequence[str]
    max_jobs: int = 1000
    posted_within_hours: int | None = None
    pacing_s: float = 1.0
    proxies: str | None = None
    timeout_s: float = 15.0


class LinkedInGuestSource:
    """Anonymous LinkedIn guest-endpoint source implementing SimpleSource."""

    def __init__(
        self,
        *,
        settings: GuestSourceSettings,
        logger: JobfeedLogger,
        fetcher: Fetcher | None = None,
        sleeper: AsyncSleeper = asyncio.sleep,
    ) -> None:
        self._settings = settings
        self._log = logger
        self._fetcher = fetcher
        self._sleep = sleeper

    async def fetch_jobs(self, config: dict[str, object]) -> list[JobPosting]:  # noqa: ARG002
        """Scrape every configured guest search URL into unenriched postings.

        Args:
            config: Protocol-satisfying no-op parameter (same convention as
                ``ATSSource``/``IndeedSource``; pass ``{}``).

        Returns:
            Deduped job postings tagged ``platform="linkedin_guest"`` with
            ``jd_text=None`` (list-card data only; JD enrichment is a later
            step), capped at ``max_jobs``.
        """
        if self._fetcher is not None:
            return await self._run(self._fetcher)
        client = create_client(self._settings.proxies, self._settings.timeout_s)
        async with client:

            async def fetch_url(url: str) -> GuestResponse:
                return await fetch(client, url, sleep=self._sleep)

            return await self._run(fetch_url)

    async def _run(self, fetcher: Fetcher) -> list[JobPosting]:
        """Execute one collection pass with run-scoped state."""
        run = _GuestRun(
            settings=self._settings,
            logger=self._log,
            fetcher=fetcher,
            sleeper=self._sleep,
        )
        return await run.collect()


def _utc_now() -> datetime:
    """Default injected clock: the current aware-UTC time."""
    return datetime.now(UTC)


class LinkedInGuestEnricher:
    """Guest posting-endpoint JD fetcher implementing the JobEnricher port.

    One ``jobPosting/{bare_id}`` GET per call, classified into the four
    ``EnrichOutcome`` signals. Stateless between calls: pacing, backoff, and
    sequencing are the enrich service's job.
    """

    def __init__(
        self,
        *,
        fetcher: Fetcher,
        logger: JobfeedLogger,
        now: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._fetch = fetcher
        self._log = logger
        self._now = now

    async def enrich(self, *, canonical_id: str, url: str) -> EnrichOutcome:  # noqa: ARG002
        """Fetch one guest posting's JD and classify the outcome.

        Args:
            canonical_id: Guest job id — the bare numeric id, tolerating a
                legacy non-digit prefix (``li-4012345678``).
            url: Protocol-satisfying public posting URL; the guest endpoint
                is derived from ``canonical_id`` instead.

        Returns:
            ``result`` on a usable JD, ``is_blocked`` on 429/999,
            ``is_gone`` on 404/410, ``error`` otherwise (including the
            retry-exhausted status-0 sentinel — a transport failure, not a
            rate-limit signal).
        """
        bare_id = _bare_id(canonical_id)
        if bare_id is None:
            self._log.warning("guest_enrich_invalid_id", canonical_id=canonical_id)
            return EnrichOutcome(error=f"invalid_canonical_id:{canonical_id}")
        response = await self._fetch(posting_url(bare_id))
        return self._classify(response, canonical_id=canonical_id)

    def _classify(self, response: GuestResponse, *, canonical_id: str) -> EnrichOutcome:
        """Map one guest response onto the EnrichOutcome signals."""
        if response.status in _BLOCKED_STATUSES:
            return EnrichOutcome(is_blocked=True)
        if response.status in _GONE_STATUSES:
            return EnrichOutcome(is_gone=True)
        if response.status != _HTTP_OK:
            self._log.warning(
                "guest_enrich_failed",
                status=response.status,
                canonical_id=canonical_id,
            )
            return EnrichOutcome(error=f"http_status:{response.status}")
        return self._from_ok_body(response.text, canonical_id=canonical_id)

    def _from_ok_body(self, html: str, *, canonical_id: str) -> EnrichOutcome:
        """Build the success result from a 200 body, or the too-short error."""
        jd_text = parse_jd(html)
        if len(jd_text) <= MIN_JD_CHARS:
            self._log.warning(
                "guest_enrich_jd_too_short",
                length=len(jd_text),
                canonical_id=canonical_id,
            )
            return EnrichOutcome(error=f"jd_too_short:len={len(jd_text)}")
        return EnrichOutcome(
            result=EnrichResult(
                jd_text=jd_text,
                quality=assess_quality(jd_text),
                enrich_source=_ENRICH_SOURCE,
                posted_at=parse_posting_posted_at(html, now=self._now()),
            )
        )


def _bare_id(canonical_id: str) -> str | None:
    """Derive the bare numeric job id from a canonical id.

    Plain digits pass through; a legacy non-digit prefix (``li-``) is
    stripped. Anything else — no digits, or trailing garbage after them —
    yields None.
    """
    match = _BARE_ID_RE.fullmatch(canonical_id)
    return match.group(1) if match else None


__all__ = [
    "MIN_JD_CHARS",
    "GuestSourceSettings",
    "LinkedInGuestEnricher",
    "LinkedInGuestSource",
]
