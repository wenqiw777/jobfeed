"""Authenticated Jobright recommendation source backed by the Chrome bridge."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

import httpx

from jobfeed.adapters.sources import _speedyapply_routing as routing
from jobfeed.adapters.sources._ats_icims import extract_jsonld_jd
from jobfeed.adapters.sources._http import ATSFetchError, fetch_text
from jobfeed.config import SourcesJobrightConfig
from jobfeed.domain.models import JobPosting, QualityBand
from jobfeed.domain.quality import assess_quality
from jobfeed.observability import JobfeedLogger
from jobfeed.ports.source import SourceFetchProgressCallback
from jobfeed.services.jobright_bridge import JobrightBridge

_PLATFORM = "jobright"
_MILLISECONDS_THRESHOLD = 10_000_000_000
_OFFICIAL_MAX_CONCURRENT = 5
_OFFICIAL_TIMEOUT_S = 30.0


class JobrightSource:
    """Fetch personalized recommendations through the connected extension."""

    def __init__(
        self,
        *,
        config: SourcesJobrightConfig,
        bridge: JobrightBridge,
        logger: JobfeedLogger,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._config = config
        self._bridge = bridge
        self._log = logger
        self._client = client

    async def fetch_jobs(self, config: dict[str, object]) -> list[JobPosting]:
        """Fetch without exposing incremental progress to the caller.

        Args:
            config: Source invocation settings reserved for interface parity.

        Returns:
            Mapped recommendations, enriched from official pages when possible.

        Raises:
            JobrightBridgeError: If the extension is unavailable or the scan fails.
        """
        return await self.fetch_jobs_with_progress(config, lambda _update: None)

    async def fetch_jobs_with_progress(
        self,
        config: dict[str, object],  # noqa: ARG002
        on_progress: SourceFetchProgressCallback,
    ) -> list[JobPosting]:
        """Fetch and map one bounded recommendation scan.

        Args:
            config: Source invocation settings reserved for interface parity.
            on_progress: Callback receiving incremental extension progress.

        Returns:
            Mapped recommendations, enriched from official pages when possible.

        Raises:
            JobrightBridgeError: If the extension is unavailable or the scan fails.
        """
        raw_jobs = await self._bridge.run_scan(
            max_jobs=self._config.max_jobs,
            batch_size=self._config.batch_size,
            pacing_s=self._config.pacing_s,
            timeout_s=self._config.timeout_s,
            on_progress=on_progress,
        )
        discovered_at = datetime.now(UTC)
        slug_cache: routing.SlugCache = {}
        semaphore = asyncio.Semaphore(_OFFICIAL_MAX_CONCURRENT)

        async def build(raw: dict[str, Any]) -> JobPosting | None:
            try:
                summary = map_jobright_job(raw, discovered_at=discovered_at)
            except ValueError as exc:
                self._log.warning("jobright_row_skipped", error=str(exc))
                return None
            if self._client is None:
                return summary
            async with semaphore:
                return await self._enrich_official(
                    raw,
                    summary,
                    discovered_at=discovered_at,
                    slug_cache=slug_cache,
                )

        built = await asyncio.gather(*(build(raw) for raw in raw_jobs))
        return [posting for posting in built if posting is not None]

    async def _enrich_official(
        self,
        raw: dict[str, Any],
        summary: JobPosting,
        *,
        discovered_at: datetime,
        slug_cache: routing.SlugCache,
    ) -> JobPosting:
        """Replace a Jobright summary only when an official ATS JD is readable."""
        assert self._client is not None
        for url in _official_urls(raw):
            try:
                result = await routing.route_and_fetch(
                    self._client,
                    url,
                    slug_cache=slug_cache,
                    timeout=_OFFICIAL_TIMEOUT_S,
                )
            except ATSFetchError as exc:
                self._log.warning(
                    "jobright_official_jd_failed", url=url, error=str(exc)
                )
                continue
            if result.enrich_source.endswith("-unrouted"):
                try:
                    jd_text = await _fetch_generic_official(
                        self._client, url, timeout=_OFFICIAL_TIMEOUT_S
                    )
                except ATSFetchError as exc:
                    self._log.warning(
                        "jobright_official_jd_failed", url=url, error=str(exc)
                    )
                    continue
                if _is_usable_official_jd(jd_text):
                    return replace(
                        summary,
                        url=url,
                        jd_text=jd_text,
                        jd_quality=QualityBand.FULL,
                        enriched_at=discovered_at,
                        enrich_source="jobright_official_jsonld",
                    )
                continue
            if not result.jd_text or result.enrich_source.endswith(
                ("-notfound", "-error")
            ):
                continue
            if not _is_usable_official_jd(result.jd_text):
                continue
            vendor = result.enrich_source.removeprefix("speedyapply-")
            return replace(
                summary,
                url=url,
                jd_text=result.jd_text,
                jd_quality=QualityBand.FULL,
                enriched_at=discovered_at,
                enrich_source=f"jobright_official_{vendor}",
            )
        return summary


async def _fetch_generic_official(
    client: httpx.AsyncClient, url: str, *, timeout: float
) -> str:
    """Visit an unrecognized employer page and read its JobPosting JSON-LD."""
    host = urlparse(url).hostname or "official"
    html_text = await fetch_text(
        client,
        url,
        slug=host,
        vendor="jobright-official",
        timeout=timeout,
    )
    return extract_jsonld_jd(html_text)


def _is_usable_official_jd(jd_text: str) -> bool:
    """Reject empty/stub official extracts before granting full provenance."""
    return assess_quality(jd_text) in {QualityBand.GOOD, QualityBand.FULL}


def map_jobright_job(raw: dict[str, Any], *, discovered_at: datetime) -> JobPosting:
    """Convert one versioned Jobright recommendation row to JobPosting.

    Args:
        raw: Versioned recommendation payload received from the extension.
        discovered_at: UTC timestamp assigned to the resulting posting.

    Returns:
        Normalized Jobfeed posting with summary provenance.

    Raises:
        ValueError: If required job or company fields are absent.
    """
    job = _mapping(raw.get("jobResult"), "jobResult")
    company = _mapping(raw.get("companyResult"), "companyResult")
    job_id = _required_text(job.get("jobId"), "jobResult.jobId")
    title = _required_text(job.get("jobTitle"), "jobResult.jobTitle")
    company_name = _required_text(
        company.get("companyName"), "companyResult.companyName"
    )
    location = _text(job.get("jobLocation")) or "Unknown"
    fallback_url = f"https://jobright.ai/jobs/info/{job_id}"
    url = _text(job.get("applyLink")) or _text(job.get("originalUrl")) or fallback_url
    jd_text = _build_jd(job)
    return JobPosting(
        platform=_PLATFORM,
        canonical_id=job_id,
        url=url,
        title=title,
        company=company_name,
        location=location,
        discovered_at=discovered_at,
        jd_text=jd_text or None,
        # Recommendation fields are useful fallback context but are not the
        # employer's original posting, regardless of their character count.
        jd_quality=QualityBand.PARTIAL if jd_text else QualityBand.MISSING,
        posted_at=_datetime(job.get("publishTime")),
        enriched_at=None,
        enrich_source="jobright_summary",
    )


def _official_urls(raw: dict[str, Any]) -> list[str]:
    """Return deduped non-Jobright employer URLs in preferred provenance order."""
    job = raw.get("jobResult")
    if not isinstance(job, dict):
        return []
    urls: list[str] = []
    for key in ("originalUrl", "applyLink"):
        url = _text(job.get(key))
        if not url or url in urls:
            continue
        host = urlparse(url).hostname or ""
        if host == "jobright.ai" or host.endswith(".jobright.ai"):
            continue
        urls.append(url)
    return urls


def _mapping(value: object, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"missing {field}")
    return value


def _required_text(value: object, field: str) -> str:
    text = _text(value)
    if not text:
        raise ValueError(f"missing {field}")
    return text


def _text(value: object) -> str:
    if isinstance(value, str | int | float):
        return str(value).strip()
    return ""


def _build_jd(job: dict[str, Any]) -> str:
    sections: list[str] = []
    fields = (
        ("Summary", "jobSummary"),
        ("Responsibilities", "coreResponsibilities"),
        ("Qualifications", "qualifications"),
        ("Detailed qualifications", "detailQualifications"),
        ("Core skills", "jdCoreSkills"),
    )
    for heading, key in fields:
        values = _flatten_text(job.get(key))
        if values:
            sections.append(f"{heading}:\n" + "\n".join(values))
    return "\n\n".join(sections)


def _flatten_text(value: object) -> list[str]:
    if isinstance(value, str | int | float):
        text = str(value).strip()
        return [text] if text else []
    if isinstance(value, list):
        return [text for item in value for text in _flatten_text(item)]
    if isinstance(value, dict):
        return [text for item in value.values() for text in _flatten_text(item)]
    return []


def _datetime(value: object) -> datetime | None:
    if isinstance(value, int | float):
        timestamp = float(value)
        if timestamp > _MILLISECONDS_THRESHOLD:
            timestamp /= 1000
        return datetime.fromtimestamp(timestamp, tz=UTC)
    if not isinstance(value, str) or not value.strip():
        return None
    normalized = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


__all__ = ["JobrightSource", "map_jobright_job"]
