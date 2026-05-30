"""Apply-URL host routing → per-vendor JD fetch for the SpeedyApply source.

SpeedyApply rows carry only an apply URL; the JD body lives behind whichever ATS
the company uses. This module matches the apply URL host against the known
vendors and dispatches to the right JD fetch, returning ``(jd_text,
enrich_source)``.

  apply_url ──host-regex──► vendor ──dispatch──► JD fetch ──► (jd_text, label)
  ─────────────────────────────────────────────────────────────────────────────
  boards[.region].greenhouse.io/<slug>/jobs/<id>  greenhouse  fetch_job (1 GET)
  jobs.ashbyhq.com/<slug>/<uuid>                  ashby       fetch_jobs (cached)
  jobs.lever.co/<slug>/<uuid>                     lever       fetch_jobs (cached)
  api: smartrecruiters / icims / workday          <vendor>    fetch_jd helper
  anything else                                   unrouted    ("", unrouted)

Ashby/Lever fetch the whole board once per slug (cached in ``slug_cache`` for the
call) and match the target posting by ``canonical_id``; a target absent from the
returned list (``fetch_jobs`` drops blank-field rows) yields
``speedyapply-notfound``. Adding a 7th vendor is a localized change: add a host
regex + a branch in ``_match_vendor`` and a fetch in ``_fetch_jd``.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx

from jobfeed.adapters.sources import _ats_ashby as ashby
from jobfeed.adapters.sources import _ats_greenhouse as greenhouse
from jobfeed.adapters.sources import _ats_icims as icims
from jobfeed.adapters.sources import _ats_lever as lever
from jobfeed.adapters.sources import _ats_smartrecruiters as smartrecruiters
from jobfeed.adapters.sources import _ats_workday as workday
from jobfeed.domain.models import JobPosting

# Slug-keyed cache of an IN-FLIGHT board fetch, shared across one fetch_jobs call
# so multiple rows from the same Ashby/Lever slug coalesce onto one network fetch.
# Caching the Task (not the resolved list) is what makes the dedup hold under the
# concurrent gather in SpeedyApplySource.fetch_jobs (see _cached_board).
SlugCache = dict[tuple[str, str], "asyncio.Task[list[JobPosting]]"]

# Greenhouse hosts: [job-]boards[.<region>].greenhouse.io/<slug>/jobs/<id>.
_GREENHOUSE_RE = re.compile(
    r"^https?://(?:job-boards|boards)(?:\.[a-z]{2})?\.greenhouse\.io/"
    r"([^/]+)/jobs/(\d+)"
)
_ASHBY_RE = re.compile(r"^https?://jobs\.ashbyhq\.com/([^/]+)/([0-9a-f-]{36})")
_LEVER_RE = re.compile(r"^https?://jobs\.lever\.co/([^/]+)/([0-9a-f-]{36})")
_SMARTRECRUITERS_RE = re.compile(r"^https?://jobs\.smartrecruiters\.com/[^/]+/\d+")
_ICIMS_RE = re.compile(r"^https?://[a-z0-9-]+\.icims\.com/jobs/\d+")
_WORKDAY_RE = re.compile(r"^https?://[^/]+\.(?:myworkdayjobs|myworkdaysite)\.com/")


@dataclass(frozen=True, kw_only=True)
class _BoardVendor:
    """A vendor matched to a full-board fetch (Ashby/Lever): cache by canonical_id."""

    vendor: str
    slug: str
    job_id: str
    fetch_jobs: Callable[..., Awaitable[list[JobPosting]]]


# A single-JD-fetch helper: (client, apply_url, timeout) -> jd_text. The vendor
# modules expose keyword-only ``timeout``; these thin wrappers normalize the call
# so the dispatch table is uniform.
_JdFetch = Callable[[httpx.AsyncClient, str, float], Awaitable[str]]


async def _fetch_smartrecruiters(
    client: httpx.AsyncClient, url: str, timeout: float
) -> str:
    return await smartrecruiters.fetch_jd(client, url, timeout=timeout)


async def _fetch_icims(client: httpx.AsyncClient, url: str, timeout: float) -> str:
    return await icims.fetch_jd(client, url, timeout=timeout)


async def _fetch_workday(client: httpx.AsyncClient, url: str, timeout: float) -> str:
    return await workday.fetch_jd(client, url, timeout=timeout)


@dataclass(frozen=True, kw_only=True)
class _JdVendor:
    """A vendor matched to a single JD fetch returning (jd_text)."""

    vendor: str
    fetch: _JdFetch


async def route_and_fetch(
    client: httpx.AsyncClient,
    apply_url: str,
    *,
    slug_cache: SlugCache,
    timeout: float,
) -> tuple[str, str]:
    """Route an apply URL to its vendor and fetch the JD.

    Args:
        client: Shared async HTTP client.
        apply_url: The row's apply URL.
        slug_cache: Per-call cache for Ashby/Lever full-board fetches.
        timeout: Per-request timeout passed to every vendor fetch.

    Returns:
        ``(jd_text, enrich_source)``. Unrouted hosts yield
        ``("", "speedyapply-unrouted")``; an Ashby/Lever id missing from its
        board yields ``("", "speedyapply-notfound")``.

    Raises:
        ATSFetchError: Propagated from the vendor fetch on HTTP/network failure
            (the caller contains it per-row).
    """
    greenhouse_match = _GREENHOUSE_RE.match(apply_url)
    if greenhouse_match:
        return await _fetch_greenhouse(client, greenhouse_match, timeout)

    board_vendor = _match_board_vendor(apply_url)
    if board_vendor is not None:
        return await _fetch_from_board(client, board_vendor, slug_cache, timeout)

    jd_vendor = _match_jd_vendor(apply_url)
    if jd_vendor is not None:
        jd_text = await jd_vendor.fetch(client, apply_url, timeout)
        return (jd_text, f"speedyapply-{jd_vendor.vendor}")

    return ("", "speedyapply-unrouted")


async def _fetch_greenhouse(
    client: httpx.AsyncClient, match: re.Match[str], timeout: float
) -> tuple[str, str]:
    """Greenhouse path: a single targeted per-job GET via fetch_job."""
    slug, job_id = match.group(1), match.group(2)
    posting = await greenhouse.fetch_job(
        client, slug, job_id, discovered_at=_now(), timeout=timeout
    )
    jd_text = posting.jd_text if posting and posting.jd_text else ""
    return (jd_text, "speedyapply-greenhouse")


def _match_board_vendor(apply_url: str) -> _BoardVendor | None:
    """Match Ashby/Lever apply URLs that need a full-board fetch + id match."""
    ashby_match = _ASHBY_RE.match(apply_url)
    if ashby_match:
        return _BoardVendor(
            vendor="ashby",
            slug=ashby_match.group(1),
            job_id=ashby_match.group(2),
            fetch_jobs=ashby.fetch_jobs,
        )
    lever_match = _LEVER_RE.match(apply_url)
    if lever_match:
        return _BoardVendor(
            vendor="lever",
            slug=lever_match.group(1),
            job_id=lever_match.group(2),
            fetch_jobs=lever.fetch_jobs,
        )
    return None


def _match_jd_vendor(apply_url: str) -> _JdVendor | None:
    """Match the single-JD-fetch vendors (SmartRecruiters / iCIMS / Workday)."""
    if _SMARTRECRUITERS_RE.match(apply_url):
        return _JdVendor(vendor="smartrecruiters", fetch=_fetch_smartrecruiters)
    if _ICIMS_RE.match(apply_url):
        return _JdVendor(vendor="icims", fetch=_fetch_icims)
    if _WORKDAY_RE.match(apply_url):
        return _JdVendor(vendor="workday", fetch=_fetch_workday)
    return None


async def _fetch_from_board(
    client: httpx.AsyncClient,
    matched: _BoardVendor,
    slug_cache: SlugCache,
    timeout: float,
) -> tuple[str, str]:
    """Fetch the vendor board once (cached) and match the target by id."""
    postings = await _cached_board(client, matched, slug_cache, timeout)
    for posting in postings:
        if posting.canonical_id == matched.job_id:
            return (posting.jd_text or "", f"speedyapply-{matched.vendor}")
    return ("", "speedyapply-notfound")


async def _cached_board(
    client: httpx.AsyncClient,
    matched: _BoardVendor,
    slug_cache: SlugCache,
    timeout: float,
) -> list[JobPosting]:
    """Return the board for (vendor, slug), fetching it at most once per call.

    The board fetch is cached as an in-flight ``asyncio.Task`` rather than its
    resolved list. The miss-check-then-store below runs with NO ``await`` in
    between, so it is atomic on the event loop: concurrent rows from the same
    slug (``SpeedyApplySource.fetch_jobs`` routes them under ``asyncio.gather``)
    all observe the stored Task and ``await`` it, coalescing onto ONE board fetch
    instead of each launching its own (a cache stampede that would re-hammer the
    vendor and break the plan's "fetch once per slug" guarantee). A failed fetch
    is cached as the failed Task for the call — every same-slug row then sees the
    same contained error, and ``_http`` has already retried transient failures.
    """
    key = (matched.vendor, matched.slug)
    task = slug_cache.get(key)
    if task is None:
        task = asyncio.ensure_future(
            matched.fetch_jobs(
                client, matched.slug, discovered_at=_now(), timeout=timeout
            )
        )
        slug_cache[key] = task
    return await task


def _now() -> datetime:
    """Current UTC time, stamped on synthesized postings for the JD fetch only."""
    return datetime.now(UTC)


__all__ = ["SlugCache", "route_and_fetch"]
