"""LinkedIn source backed by python-jobspy (SimpleSource).

Why JobSpy and not the Playwright LinkedIn SessionSource? JobSpy hits LinkedIn's
public guest job-search endpoint through ``tls-client`` (a real-Chrome TLS
fingerprint) — no browser, no login, no cookie profile. It complements (does not
replace) the authenticated Playwright source: this path is anonymous and inline.

This source is a thin shell: it delegates the entire per-URL scrape loop to the
shared ``_jobspy_process.scrape_urls`` (the SAME loop Indeed JobSpy reuses — the
child-process timeout + per-URL error containment lives there, never here).
Unlike Indeed, there is NO ``dateOnIndeed`` date patch (that knob is
Indeed-specific). JobSpy returns each posting fully populated with an inline JD,
so ``fetch_jobs`` returns ready-to-save postings and no later enrich step is
needed.

The platform tag is ``linkedin_jobspy`` (distinct from the scraped
``site_name="linkedin"``) so anonymous JobSpy rows never collide with the
authenticated LinkedIn SessionSource's ``linkedin`` postings (Decision 5).
"""

from __future__ import annotations

from datetime import UTC, datetime

from jobfeed.adapters.sources import _jobspy_process
from jobfeed.config import SourcesLinkedInJobSpyConfig
from jobfeed.domain.models import JobPosting
from jobfeed.observability import JobfeedLogger

_PLATFORM = "linkedin_jobspy"
_SITE_NAME = "linkedin"


class LinkedInJobSpySource:
    """Public-facing LinkedIn (JobSpy) source adapter implementing SimpleSource."""

    def __init__(
        self,
        *,
        config: SourcesLinkedInJobSpyConfig,
        logger: JobfeedLogger,
    ) -> None:
        self._config = config
        self._log = logger

    async def fetch_jobs(self, config: dict[str, object]) -> list[JobPosting]:  # noqa: ARG002
        """Scrape every configured LinkedIn search URL via JobSpy.

        Args:
            config: Protocol-satisfying no-op parameter (same convention as
                ``ATSSource``/``IndeedSource``; pass ``{}``).

        Returns:
            Fully-populated job postings (inline JD) tagged
            ``platform="linkedin_jobspy"``.
        """
        return await _jobspy_process.scrape_urls(
            site_name=_SITE_NAME,
            platform=_PLATFORM,
            search_urls=self._config.search_urls,
            max_jobs=self._config.max_jobs,
            hours_old=self._config.hours_old,
            timeout_s=self._config.timeout_s,
            max_concurrent=self._config.max_concurrent,
            logger=self._log,
            discovered_at=datetime.now(UTC),
        )


__all__ = ["LinkedInJobSpySource"]
