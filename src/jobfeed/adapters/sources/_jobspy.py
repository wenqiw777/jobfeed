"""JobSpy isolation boundary: site-aware URL parse + DataFrame -> JobPosting.

This module is the ONLY place pandas / jobspy / tls-client types are allowed to
exist. Callers (``indeed_jobspy``, ``linkedin_jobspy``) see only
``list[JobPosting]`` and ``JobSpyError``; nothing pandas-shaped escapes.

Pipeline (ASCII)::

    search_url ──parse──▶ JobSpy kwargs ──scrape_jobs()──▶ pandas.DataFrame
                (site-aware)                                     │
                                                                 ▼
    list[JobPosting]  ◀──row->posting (NaN->None, date->UTC)── DataFrame rows

``scrape`` runs ONE synchronous ``jobspy.scrape_jobs`` call (jobspy/pandas are
lazy-imported inside it, so importing this module is cheap and does not require
jobspy installed). ``scrape_urls`` is the shared async fan-out both JobSpy
sources reuse: each URL runs inside a child process so configured timeouts can
stop a hung JobSpy scrape; per-URL errors are contained so one bad URL never
aborts the rest.

The pure-stdlib URL -> kwargs parsing lives in ``_jobspy_url.py`` (split out to
keep this module under the 300-line gate); the DataFrame -> JobPosting
conversion (the only pandas-touching code) stays here.
"""

from __future__ import annotations

import asyncio
import multiprocessing as mp
from dataclasses import dataclass
from datetime import UTC, date, datetime
from multiprocessing.queues import Queue
from queue import Empty
from typing import TYPE_CHECKING, Any

from jobfeed.adapters.sources._jobspy_url import parse_search_url
from jobfeed.domain.models import JobPosting
from jobfeed.domain.quality import assess_quality
from jobfeed.observability import JobfeedLogger

if TYPE_CHECKING:  # pragma: no cover - typing only, never imported at runtime
    import pandas as pd

_PROCESS_START_METHOD = "spawn"
_PROCESS_KILL_GRACE_S = 1.0
_PROCESS_RESULT_GRACE_S = 0.5


class JobSpyError(RuntimeError):
    """Raised when a JobSpy scrape fails (Cloudflare challenge, exception).

    Contains the originating ``site_name`` / ``search_url`` so callers can log
    which URL was blocked without re-parsing the message.
    """

    def __init__(self, message: str, *, site_name: str, search_url: str) -> None:
        super().__init__(message)
        self.site_name = site_name
        self.search_url = search_url


@dataclass(frozen=True, kw_only=True)
class _ScrapeRequest:
    site_name: str
    platform: str
    search_url: str
    max_jobs: int
    hours_old: int | None
    country_indeed: str | None
    discovered_at: datetime


@dataclass(frozen=True, kw_only=True)
class _ScrapeProcessOutcome:
    postings: list[JobPosting]
    error: str | None = None
    timed_out: bool = False


def scrape(  # noqa: PLR0913 - each arg is a distinct scrape input, all required
    *,
    site_name: str,
    platform: str,
    search_url: str,
    max_jobs: int,
    hours_old: int | None,
    country_indeed: str | None = None,
    discovered_at: datetime | None = None,
) -> list[JobPosting]:
    """Scrape ONE JobSpy search URL and return fully-populated postings.

    jobspy/pandas are imported INSIDE this function so the module stays
    import-cheap and importable without jobspy present.

    Args:
        site_name: JobSpy site (``"indeed"`` or ``"linkedin"``); drives URL
            parsing and the ``scrape_jobs`` call.
        platform: Platform tag stamped on each ``JobPosting`` (kept distinct
            from ``site_name`` so ``linkedin_jobspy`` tags differ from the
            scraped site, per Decision 5).
        search_url: A user-pasted search URL; query params become kwargs.
        max_jobs: Cap passed to ``results_wanted``.
        hours_old: When not None, OVERRIDES any freshness window in the URL
            (Indeed ``fromage`` / LinkedIn ``f_TPR``).
        country_indeed: JobSpy country selector for Indeed searches.
        discovered_at: Scan-start timestamp; defaults to ``now`` if omitted.

    Returns:
        Job postings with inline JD (``enrich_source="jobspy_inline"``).

    Raises:
        JobSpyError: On any scrape failure (challenge, transport, jobspy error).
    """
    import jobspy  # noqa: PLC0415 — lazy: jobspy pulls pandas + tls-client (heavy)

    stamp = discovered_at or datetime.now(UTC)
    kwargs = parse_search_url(site_name, search_url)
    if hours_old is not None:
        kwargs["hours_old"] = hours_old
    if site_name == "linkedin":
        # JobSpy populates the LinkedIn `description` column ONLY when asked; the
        # default omits it, so without this every LinkedIn row would persist with
        # jd_text=None despite our enrich_source="jobspy_inline" contract. Costs
        # one extra detail fetch per job (the price of an inline JD via JobSpy).
        kwargs["linkedin_fetch_description"] = True

    try:
        frame = jobspy.scrape_jobs(
            site_name=site_name,
            results_wanted=max_jobs,
            country_indeed=country_indeed or "usa",
            **kwargs,
        )
    except Exception as exc:  # contain every jobspy/tls-client failure
        raise JobSpyError(
            f"jobspy scrape failed for {site_name} {search_url}: {exc}",
            site_name=site_name,
            search_url=search_url,
        ) from exc

    return _frame_to_postings(frame, platform=platform, discovered_at=stamp)


async def scrape_urls(  # noqa: PLR0913 - shared loop needs each scrape input
    *,
    site_name: str,
    platform: str,
    search_urls: list[str],
    max_jobs: int,
    hours_old: int | None,
    timeout_s: float,
    max_concurrent: int,
    logger: JobfeedLogger,
    discovered_at: datetime,
    country_indeed: str | None = None,
) -> list[JobPosting]:
    """Scrape every search URL off the event loop, containing per-URL errors.

    Shared by both JobSpy sources (Indeed + LinkedIn). Each URL's synchronous
    ``scrape`` runs in a child process while the parent waits from a bounded
    worker thread, so the event loop is never blocked and a hung JobSpy scrape
    can be terminated. A failure on one URL is logged and skipped so the
    remaining URLs still contribute their postings.

    Args:
        site_name: JobSpy site passed through to ``scrape``.
        platform: Platform tag passed through to ``scrape`` (and onto postings).
        search_urls: Search URLs to scrape, in order.
        max_jobs: Per-URL cap.
        hours_old: Freshness override (see ``scrape``).
        timeout_s: Per-URL wall-clock timeout for the blocking JobSpy call.
        max_concurrent: Maximum number of URLs scraped concurrently.
        logger: Structured logger for contained per-URL failures.
        discovered_at: Scan-start timestamp stamped on every posting.
        country_indeed: JobSpy country selector for Indeed searches.

    Returns:
        Aggregated postings across all URLs that did not fail.
    """
    sem = asyncio.Semaphore(max_concurrent)
    batches = await asyncio.gather(
        *[
            _scrape_one_url(
                sem=sem,
                site_name=site_name,
                platform=platform,
                url=url,
                max_jobs=max_jobs,
                hours_old=hours_old,
                timeout_s=timeout_s,
                logger=logger,
                discovered_at=discovered_at,
                country_indeed=country_indeed,
            )
            for url in search_urls
        ]
    )
    return [posting for batch in batches for posting in batch]


def _run_scrape_process(
    request: _ScrapeRequest, timeout_s: float
) -> _ScrapeProcessOutcome:
    ctx: Any = mp.get_context(_PROCESS_START_METHOD)
    result_queue: Queue[_ScrapeProcessOutcome] = ctx.Queue(maxsize=1)
    process = ctx.Process(
        target=_scrape_child_main,
        args=(request, result_queue),
        daemon=True,
    )
    try:
        process.start()
        process.join(timeout_s)
        if process.is_alive():
            _stop_process(process)
            return _ScrapeProcessOutcome(postings=[], timed_out=True)
        return _read_scrape_process_result(process, result_queue)
    finally:
        result_queue.close()
        result_queue.join_thread()


def _scrape_child_main(
    request: _ScrapeRequest, result_queue: Queue[_ScrapeProcessOutcome]
) -> None:
    try:
        postings = scrape(
            site_name=request.site_name,
            platform=request.platform,
            search_url=request.search_url,
            max_jobs=request.max_jobs,
            hours_old=request.hours_old,
            country_indeed=request.country_indeed,
            discovered_at=request.discovered_at,
        )
    except Exception as exc:  # contain every child-side jobspy/tls-client failure
        result_queue.put(_ScrapeProcessOutcome(postings=[], error=str(exc)))
        return
    result_queue.put(_ScrapeProcessOutcome(postings=postings))


def _stop_process(process: Any) -> None:
    process.terminate()
    process.join(_PROCESS_KILL_GRACE_S)
    if not process.is_alive():
        return
    process.kill()
    process.join(_PROCESS_KILL_GRACE_S)


def _read_scrape_process_result(
    process: Any, result_queue: Queue[_ScrapeProcessOutcome]
) -> _ScrapeProcessOutcome:
    try:
        result = result_queue.get(timeout=_PROCESS_RESULT_GRACE_S)
    except Empty:
        exitcode = getattr(process, "exitcode", None)
        return _ScrapeProcessOutcome(
            postings=[],
            error=f"jobspy child exited without result (exitcode={exitcode})",
        )
    if isinstance(result, _ScrapeProcessOutcome):
        return result
    return _ScrapeProcessOutcome(
        postings=[],
        error=f"jobspy child returned unexpected result type: {type(result).__name__}",
    )


async def _scrape_one_url(  # noqa: PLR0913 - carries the shared scrape contract
    *,
    sem: asyncio.Semaphore,
    site_name: str,
    platform: str,
    url: str,
    max_jobs: int,
    hours_old: int | None,
    timeout_s: float,
    logger: JobfeedLogger,
    discovered_at: datetime,
    country_indeed: str | None,
) -> list[JobPosting]:
    async with sem:
        request = _ScrapeRequest(
            site_name=site_name,
            platform=platform,
            search_url=url,
            max_jobs=max_jobs,
            hours_old=hours_old,
            country_indeed=country_indeed,
            discovered_at=discovered_at,
        )
        try:
            outcome = await asyncio.to_thread(_run_scrape_process, request, timeout_s)
        except Exception as exc:
            logger.warning(
                "jobspy_scrape_failed", site=site_name, url=url, error=str(exc)
            )
            return []
        if outcome.timed_out:
            logger.warning(
                "jobspy_scrape_timed_out",
                site=site_name,
                url=url,
                timeout_s=timeout_s,
            )
            return []
        if outcome.error is not None:
            logger.warning(
                "jobspy_scrape_failed", site=site_name, url=url, error=outcome.error
            )
            return []
        return outcome.postings


# ---------------------------------------------------------------------------
# DataFrame -> JobPosting (pandas confined here)
# ---------------------------------------------------------------------------


def _frame_to_postings(
    frame: pd.DataFrame | None, *, platform: str, discovered_at: datetime
) -> list[JobPosting]:
    """Convert a JobSpy DataFrame into JobPostings, skipping unusable rows.

    Args:
        frame: The DataFrame returned by ``scrape_jobs`` (or None / empty).
        platform: Platform tag for each posting.
        discovered_at: Scan-start timestamp.

    Returns:
        One JobPosting per row that has the required id/url/title fields.
    """
    if frame is None or len(frame) == 0:
        return []
    postings: list[JobPosting] = []
    for _, row in frame.iterrows():
        posting = _row_to_posting(
            row.to_dict(), platform=platform, discovered_at=discovered_at
        )
        if posting is not None:
            postings.append(posting)
    return postings


def _row_to_posting(
    row: dict[str, Any], *, platform: str, discovered_at: datetime
) -> JobPosting | None:
    """Build one JobPosting from a JobSpy row dict (NaN/NaT coerced to None).

    JobSpy columns used: ``id``, ``title``, ``company``, ``location``,
    ``job_url``, ``description`` (inline JD), ``date_posted``.

    Args:
        row: One DataFrame row as a plain dict.
        platform: Platform tag for the posting.
        discovered_at: Scan-start timestamp.

    Returns:
        A populated JobPosting, or None when id/url/title are missing.
    """
    canonical_id = _clean(row.get("id"))
    job_url = _clean(row.get("job_url"))
    title = _clean(row.get("title"))
    if not (canonical_id and job_url and title):
        return None
    jd_text = _clean(row.get("description"))
    return JobPosting(
        platform=platform,
        canonical_id=canonical_id,
        url=job_url,
        title=title,
        company=_clean(row.get("company")) or "",
        location=_clean(row.get("location")) or "",
        discovered_at=discovered_at,
        jd_text=jd_text or None,
        jd_quality=assess_quality(jd_text or None),
        posted_at=_coerce_posted_at(row.get("date_posted")),
        # Only claim inline-JD provenance when JobSpy actually returned a
        # description; a row without one stays unenriched (None/None) so it is
        # not mistaken for an enriched posting (consistent with SpeedyApply).
        enriched_at=discovered_at if jd_text else None,
        enrich_source="jobspy_inline" if jd_text else None,
    )


def _clean(value: Any) -> str:
    """Stringify a cell, mapping pandas NaN / "nan" sentinels to "".

    Uses ``pandas.isna`` so real NaN/NaT scalars collapse to empty before the
    string-level ``"nan"`` guard catches values that were stringified upstream.
    """
    if value is None or _is_pandas_na(value):
        return ""
    text = str(value).strip()
    return "" if text.lower() in ("nan", "nat", "none") else text


def _coerce_posted_at(value: Any) -> datetime | None:
    """Normalize JobSpy's ``date_posted`` cell into an aware-UTC datetime.

    Handles ``datetime`` / ``date`` objects, ISO strings, and pandas NaN/NaT
    (via ``pandas.isna``). Naive datetimes are pinned to UTC.

    Args:
        value: The raw ``date_posted`` cell.

    Returns:
        An aware-UTC datetime, or None when the cell is missing/unparseable.
    """
    if value is None or _is_pandas_na(value):
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=UTC)
    text = str(value).strip()
    if not text or text.lower() in ("nan", "nat", "none"):
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _is_pandas_na(value: Any) -> bool:
    """True when ``value`` is a pandas NaN/NaT scalar.

    pandas is imported lazily here too; ``pandas.isna`` on a list/array returns
    an array (ambiguous truthiness), so only scalars are tested.
    """
    import pandas as pd  # noqa: PLC0415 — lazy: keep module import-cheap

    result = pd.isna(value)
    return bool(result) if isinstance(result, bool) else False


__all__ = ["JobSpyError", "scrape", "scrape_urls"]
