"""Reuse LinkedIn evidence and read missing details only through anonymous Guest."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx

from jobfeed.adapters.sources._linkedin_guest_http import create_client, posting_url
from jobfeed.adapters.sources._linkedin_guest_parse import parse_apply_url, parse_jd
from jobfeed.adapters.sources._linkedin_guest_shared import shared_fetch
from jobfeed.domain.external_identity import observed_identifier
from jobfeed.domain.models import JobPosting

Lookup = Callable[[str], Awaitable[JobPosting | None]]
_MIN_JD = 200


def is_linkedin_url(url: str) -> bool:
    """Match LinkedIn and its subdomains, never similarly named hosts.

    Args:
        url: Candidate URL whose hostname should be checked.

    Returns:
        Whether the hostname is LinkedIn or one of its subdomains.
    """
    host = (urlsplit(url).hostname or "").lower().rstrip(".")
    return host == "linkedin.com" or host.endswith(".linkedin.com")


@dataclass(frozen=True)
class LinkedInEvidence:
    """Observed external link and body; absent links do not authorize browsing."""

    apply_url: str | None = None
    jd_text: str | None = None
    reason: str = "linkedin_existing_data"


class LinkedInApplicationReader:
    """Prefer source data and saved native IDs over additional Guest requests."""

    def __init__(self, lookup: Lookup | None = None) -> None:
        self.lookup = lookup

    async def __call__(self, job: JobPosting, url: str) -> LinkedInEvidence:
        identity = observed_identifier(url)
        if identity is None or identity.provider != "linkedin":
            return LinkedInEvidence(reason="linkedin_job_id_missing")
        saved = await self.lookup(identity.native_id) if self.lookup else None
        if saved is not None:
            link = saved.apply_url
            if link and is_linkedin_url(link):
                link = None
            return LinkedInEvidence(link, saved.jd_text)
        if len(job.jd_text or "") > _MIN_JD:
            return LinkedInEvidence(jd_text=job.jd_text)
        # A fresh HTTP client has no Chrome session cookies or credentials.
        async with create_client(None, 15.0) as client:
            response = await shared_fetch(client, posting_url(identity.native_id))
        if response.status != httpx.codes.OK:
            return LinkedInEvidence(reason="linkedin_guest_unavailable")
        return LinkedInEvidence(
            parse_apply_url(response.text),
            parse_jd(response.text),
            "linkedin_guest_no_external_link",
        )
