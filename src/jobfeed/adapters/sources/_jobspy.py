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
jobspy installed). The pure-stdlib URL -> kwargs parsing lives in
``_jobspy_url.py``; the subprocess isolation + async fan-out both JobSpy sources
reuse (``scrape_urls``) lives in ``_jobspy_process.py`` (it contains no pandas
and just orchestrates this module's ``scrape``). Both were split out to keep
each file under the 300-line gate and the pandas-touching code confined here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any

from jobfeed.adapters.sources._jobspy_patches import apply_indeed_date_patch
from jobfeed.adapters.sources._jobspy_url import parse_search_url
from jobfeed.domain.models import JobPosting
from jobfeed.domain.quality import assess_quality

if TYPE_CHECKING:  # pragma: no cover - typing only, never imported at runtime
    import pandas as pd


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
class ScrapeConfig:
    """Scrape behaviour knobs passed through to jobspy.scrape_jobs.

    Bundled into a dataclass so ``scrape`` stays under the 5-argument limit.

    Attributes:
        max_jobs: Cap passed to ``results_wanted``.
        hours_old: When not None, overrides any freshness window in the URL.
        country_indeed: JobSpy country selector for Indeed searches.
    """

    max_jobs: int
    hours_old: int | None
    country_indeed: str | None = None


def scrape(
    *,
    site_name: str,
    platform: str,
    search_url: str,
    config: ScrapeConfig,
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
        config: Scrape behaviour knobs (max_jobs, hours_old, country_indeed).
        discovered_at: Scan-start timestamp; defaults to ``now`` if omitted.

    Returns:
        Job postings with inline JD (``enrich_source="jobspy_inline"``).

    Raises:
        JobSpyError: On any scrape failure (challenge, transport, jobspy error).
    """
    import jobspy  # noqa: PLC0415 — lazy: jobspy pulls pandas + tls-client (heavy)

    stamp = discovered_at or datetime.now(UTC)
    kwargs = parse_search_url(site_name, search_url)
    if config.hours_old is not None:
        kwargs["hours_old"] = config.hours_old
    if site_name == "linkedin":
        # JobSpy populates the LinkedIn `description` column ONLY when asked; the
        # default omits it, so without this every LinkedIn row would persist with
        # jd_text=None despite our enrich_source="jobspy_inline" contract. Costs
        # one extra detail fetch per job (the price of an inline JD via JobSpy).
        kwargs["linkedin_fetch_description"] = True
    if site_name == "indeed":
        # Apply the dateOnIndeed patch HERE, where jobspy actually runs, not only
        # in IndeedSource.fetch_jobs(): scrape() executes inside a `spawn` child
        # process (see _jobspy_process), which gets a fresh interpreter and does
        # NOT inherit the parent's monkeypatch. Without this, every Indeed
        # subprocess scrape silently falls back to JobSpy's unpatched
        # datePublished mapping. The patch is idempotent, so the parent's
        # early-fail-loud call remains harmless.
        apply_indeed_date_patch()

    try:
        frame = jobspy.scrape_jobs(
            site_name=site_name,
            results_wanted=config.max_jobs,
            country_indeed=config.country_indeed or "usa",
            **kwargs,
        )
    except Exception as exc:  # contain every jobspy/tls-client failure
        raise JobSpyError(
            f"jobspy scrape failed for {site_name} {search_url}: {exc}",
            site_name=site_name,
            search_url=search_url,
        ) from exc

    return _frame_to_postings(frame, platform=platform, discovered_at=stamp)


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


__all__ = ["JobSpyError", "ScrapeConfig", "scrape"]
