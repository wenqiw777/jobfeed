"""ATSSource facade: concurrent company scanning with probe and error recovery."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from jobfeed.adapters.sources import _ats_ashby as ashby
from jobfeed.adapters.sources import _ats_greenhouse as greenhouse
from jobfeed.adapters.sources import _ats_lever as lever
from jobfeed.adapters.sources._ats_probe import probe_company, resolve_dead_slug
from jobfeed.adapters.sources._http import (
    ATSFetchError,
    ATSParseError,
    ProbeIndeterminateError,
    ProbeNetworkError,
)
from jobfeed.adapters.sources._target_titles import filter_target_titles
from jobfeed.config import SourcesATSConfig
from jobfeed.domain.models import CompanyRecord, JobPosting
from jobfeed.observability import JobfeedLogger
from jobfeed.ports.store_ops import StoreOpsMixin

SUPPORTED_VENDORS: frozenset[str] = frozenset({"greenhouse", "ashby", "lever"})

_VENDOR_FETCHERS: dict[
    str, Callable[..., Coroutine[object, object, list[JobPosting]]]
] = {
    "greenhouse": greenhouse.fetch_jobs,
    "ashby": ashby.fetch_jobs,
    "lever": lever.fetch_jobs,
}

_DEAD_STATUSES = frozenset({404, 410})


class ATSSource:
    """Public-facing ATS source adapter implementing SimpleSource."""

    def __init__(
        self,
        *,
        client: httpx.AsyncClient,
        store: StoreOpsMixin,
        config: SourcesATSConfig,
        logger: JobfeedLogger,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._client = client
        self._store = store
        self._config = config
        self._log = logger
        # Injectable wall clock so freshness/probe-TTL logic is deterministic in
        # tests (default: real UTC now). Without this, a test that pins a fixed
        # date drifts against datetime.now() and its "fresh" fixtures silently
        # rot once real time passes probe_ttl_days.
        self._now = now or (lambda: datetime.now(UTC))

    async def fetch_jobs(self, config: dict[str, object]) -> list[JobPosting]:  # noqa: ARG002
        """Fetch jobs from all tracked ATS companies concurrently.

        Args:
            config: Protocol-satisfying no-op parameter.

        Returns:
            Aggregated job postings from all companies.
        """
        companies = await self._store.list_companies()
        scan_started_at = self._now()
        sem = asyncio.Semaphore(self._config.max_concurrent)
        tasks = [
            self._process_company(c, scan_started_at, sem)
            for c in companies
            if _should_process(c)
        ]
        results = await asyncio.gather(*tasks)
        all_jobs: list[JobPosting] = []
        for job_list in results:
            all_jobs.extend(job_list)
        relevant = filter_target_titles(all_jobs, self._config.title_keywords)
        return relevant[: self._config.max_jobs]

    async def _process_company(
        self, company: CompanyRecord, started: datetime, sem: asyncio.Semaphore
    ) -> list[JobPosting]:
        """Process one company under semaphore; never raises."""
        async with sem:
            try:
                return await self._do_company(company, started)
            except Exception as exc:
                self._log.error(
                    "unexpected_company_error", slug=company.slug, error=str(exc)
                )
                return []

    async def _do_company(
        self, company: CompanyRecord, started: datetime
    ) -> list[JobPosting]:
        """Resolve vendor, fetch, handle errors."""
        vendor = await self._resolve_vendor(company, started)
        if vendor is None:
            return []
        try:
            jobs = await self._vendor_fetch(vendor, company.slug, started)
        except (ATSFetchError, ATSParseError) as exc:
            return await self._handle_fetch_error(exc, company)
        await self._handle_success(company, len(jobs), started)
        return jobs

    async def _resolve_vendor(
        self, company: CompanyRecord, now: datetime
    ) -> str | None:
        """Determine vendor via cache or probe."""
        if company.ats_vendor is None:
            return await self._probe_unknown(company, now)
        if self._is_stale(company) and not company.ats_override:
            return await self._refresh_known(company, now)
        return company.ats_vendor

    async def _probe_unknown(self, company: CompanyRecord, now: datetime) -> str | None:
        """Probe a company with no cached vendor."""
        if not self._is_stale(company):
            return None
        try:
            vendor = await probe_company(
                self._client, company.slug, timeout=self._config.probe_timeout_s
            )
        except (ProbeNetworkError, ProbeIndeterminateError) as exc:
            self._log.warning("probe_error", slug=company.slug, error=str(exc))
            await self._update_company(company, last_probe_attempt_at=now)
            return None
        if vendor is not None:
            await self._update_company(
                company,
                ats_vendor=vendor,
                last_verified_at=now,
                last_probe_attempt_at=now,
            )
            return vendor
        await self._update_company(
            company, last_verified_at=now, last_probe_attempt_at=now
        )
        return None

    async def _refresh_known(self, company: CompanyRecord, now: datetime) -> str:
        """Re-probe a known-vendor company with stale TTL."""
        try:
            vendor = await probe_company(
                self._client, company.slug, timeout=self._config.probe_timeout_s
            )
        except (ProbeNetworkError, ProbeIndeterminateError) as exc:
            self._log.warning("refresh_probe_error", slug=company.slug, error=str(exc))
            await self._update_company(company, last_probe_attempt_at=now)
            return company.ats_vendor  # type: ignore[return-value]
        if vendor is None:
            await self._update_company(company, last_probe_attempt_at=now)
            return company.ats_vendor  # type: ignore[return-value]
        await self._update_company(
            company, ats_vendor=vendor, last_verified_at=now, last_probe_attempt_at=now
        )
        return vendor

    async def _vendor_fetch(
        self, vendor: str, slug: str, scan_started_at: datetime
    ) -> list[JobPosting]:
        """Dispatch to the appropriate vendor fetch function."""
        fetcher = _VENDOR_FETCHERS[vendor]
        return await fetcher(
            self._client,
            slug,
            discovered_at=scan_started_at,
            timeout=self._config.scan_timeout_s,
        )

    async def _handle_fetch_error(
        self, exc: ATSFetchError | ATSParseError, company: CompanyRecord
    ) -> list[JobPosting]:
        """Route fetch errors to the correct handler."""
        if isinstance(exc, ATSParseError):
            self._log.warning("parse_error", slug=company.slug, error=str(exc))
            return []
        if exc.status_code is None:
            self._log.warning("network_error", slug=company.slug, error=str(exc))
            return []
        if exc.status_code not in _DEAD_STATUSES:
            self._log.warning("http_error", slug=company.slug, status=exc.status_code)
            return []
        return await self._handle_dead_board(company)

    async def _handle_dead_board(self, company: CompanyRecord) -> list[JobPosting]:
        """Handle 404/410: bump counter, possibly resolve dead slug."""
        count = await self._store.bump_discover_failure(company.slug)
        if count < self._config.failure_threshold:
            self._log.info("dead_board_below_threshold", slug=company.slug, count=count)
            return []
        return await self._attempt_recovery(company)

    async def _attempt_recovery(self, company: CompanyRecord) -> list[JobPosting]:
        """Run resolve_dead_slug at threshold."""
        try:
            new_vendor = await resolve_dead_slug(
                self._client, company.slug, timeout=self._config.probe_timeout_s
            )
        except (ProbeNetworkError, ProbeIndeterminateError) as exc:
            self._log.warning("resolve_unresolved", slug=company.slug, error=str(exc))
            return []
        if new_vendor is None:
            await self._store.mark_company_removed(company.slug)
            await self._store.reset_discover_failures(company.slug)
            self._log.info("company_removed", slug=company.slug)
            return []
        now = self._now()
        await self._update_company(
            company,
            ats_vendor=new_vendor,
            last_verified_at=now,
            last_probe_attempt_at=now,
        )
        return await self._retry_fetch(company, new_vendor, now)

    async def _retry_fetch(
        self, company: CompanyRecord, vendor: str, now: datetime
    ) -> list[JobPosting]:
        """Retry fetch after recovery."""
        try:
            jobs = await self._vendor_fetch(vendor, company.slug, now)
        except (ATSFetchError, ATSParseError) as retry_exc:
            self._log.warning("retry_error", slug=company.slug, error=str(retry_exc))
            return []
        await self._store.reset_discover_failures(company.slug)
        await self._update_company(
            company, job_count_last_scan=len(jobs), last_verified_at=now
        )
        return jobs

    async def _handle_success(
        self, company: CompanyRecord, job_count: int, now: datetime
    ) -> None:
        """Post-success: reset failures, update scan count and verified_at."""
        await self._store.reset_discover_failures(company.slug)
        await self._update_company(
            company, job_count_last_scan=job_count, last_verified_at=now
        )

    async def _update_company(self, company: CompanyRecord, **fields: Any) -> None:
        """Read-modify-write: read existing row, apply field changes, upsert."""
        existing = await self._store.get_company(company.slug)
        if existing is None:
            return
        updated = replace(existing, **fields)
        await self._store.upsert_company(updated)

    def _is_stale(self, company: CompanyRecord) -> bool:
        """True when last_verified_at is missing or older than probe_ttl_days."""
        if company.last_verified_at is None:
            return True
        age = self._now() - company.last_verified_at
        return age > timedelta(days=self._config.probe_ttl_days)


def _should_process(company: CompanyRecord) -> bool:
    """True when vendor is None (needs probe) or in SUPPORTED_VENDORS."""
    if company.ats_vendor is None:
        return True
    return company.ats_vendor in SUPPORTED_VENDORS


__all__ = ["SUPPORTED_VENDORS", "ATSSource"]
