"""Indeed source backed by python-jobspy (SimpleSource).

Why JobSpy and not Playwright? Indeed sits behind Cloudflare's managed
challenge, which fingerprints headless browsers. JobSpy hits Indeed's internal
GraphQL API through ``tls-client`` (a real-Chrome TLS fingerprint) — no
browser, no challenge to clear, no login.

This source is a thin shell: it applies the Indeed ``dateOnIndeed`` date patch
once, then delegates the entire per-URL scrape loop to the shared
``_jobspy.scrape_urls`` (the SAME loop LinkedIn JobSpy reuses — the
``asyncio.to_thread`` + per-URL error containment lives there, never here).
JobSpy returns each posting fully populated with an inline JD, so ``fetch_jobs``
returns ready-to-save postings and no later enrich step is needed.
"""

from __future__ import annotations

from datetime import UTC, datetime

from jobfeed.adapters.sources import _jobspy
from jobfeed.adapters.sources._jobspy_patches import apply_indeed_date_patch
from jobfeed.config import SourcesIndeedConfig
from jobfeed.domain.models import JobPosting
from jobfeed.observability import JobfeedLogger

_PLATFORM = "indeed"
_SITE_NAME = "indeed"


class IndeedSource:
    """Public-facing Indeed source adapter implementing SimpleSource."""

    def __init__(
        self,
        *,
        config: SourcesIndeedConfig,
        logger: JobfeedLogger,
    ) -> None:
        self._config = config
        self._log = logger

    async def fetch_jobs(self, config: dict[str, object]) -> list[JobPosting]:  # noqa: ARG002
        """Scrape every configured Indeed search URL via JobSpy.

        Args:
            config: Protocol-satisfying no-op parameter (same convention as
                ``ATSSource``/``SpeedyApplySource``; pass ``{}``).

        Returns:
            Fully-populated job postings (inline JD) tagged ``platform="indeed"``.
        """
        apply_indeed_date_patch()
        return await _jobspy.scrape_urls(
            site_name=_SITE_NAME,
            platform=_PLATFORM,
            search_urls=self._config.search_urls,
            max_jobs=self._config.max_jobs,
            hours_old=self._config.hours_old,
            logger=self._log,
            discovered_at=datetime.now(UTC),
        )


__all__ = ["IndeedSource"]
