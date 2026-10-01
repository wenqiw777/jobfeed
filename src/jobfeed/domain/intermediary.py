"""Conservative attribution of intermediary listings to observed ATS postings."""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from urllib.parse import urlsplit

from jobfeed.domain.external_identity import observed_identifier
from jobfeed.domain.models import JobPosting, QualityBand
from jobfeed.domain.normalize import normalize, normalize_company

BLOCKED_PUBLISHERS = (
    "yara ai",
    "remotehunter",
    "remote hunter",
    "torentify",
    "talenthop",
    "sundayy",
    "netrolynx ai",
    "fetchjobs.co",
    "ladders",
    "the ladders",
    "jobgether",
    "wiraa",
    "jack & jill",
    (
        "underdog: verified engineers. interview-ready. searching "
        "confidentially. and companies apply to you"
    ),
    (
        "underdog.io -apply to top tech jobs in 60 seconds. "
        "a place where companies apply to you"
    ),
    "dex",
    "talentally",
    "coderound ai",
    "haystack",
    "hackajob",
    "hire feed",
    "jobverse.io",
    "underdog.io",
)
PUBLISHERS = (
    "dice",
    "jobs via dice",
    "jobverse",
    *BLOCKED_PUBLISHERS,
)
DOMAINS = ("dice.com", "jobverse.io")
_MIN_WORDS = 80
_MIN_DISTINCT_WORDS = 60
_MAX_WORDS = 5000
_MIN_SIMILARITY = 0.90
_MIN_SOURCE_COVERAGE = 0.98
_MIN_OFFICIAL_COVERAGE = 0.70


def blocked_publisher_company(company: str | None) -> bool:
    """Match publishers excluded before persistence, resolution and scoring.

    Args:
        company: Observed publisher name.

    Returns:
        Whether the whole name matches a blocked publisher.
    """
    return " ".join((company or "").casefold().split()) in BLOCKED_PUBLISHERS


def intermediary_values(company: str, url: str, apply_url: str | None = None) -> bool:
    """Identify named publishers and exact domain/subdomain boundaries.

    Args:
        company: Source display company.
        url: Observed source URL.
        apply_url: Observed application URL, if present.

    Returns:
        Whether this posting requires attribution before evaluation.
    """
    if " ".join(company.casefold().split()) in PUBLISHERS:
        return True
    for value in (url, apply_url):
        try:
            host = (urlsplit(value or "").hostname or "").lower()
        except ValueError:
            continue
        if any(host == domain or host.endswith("." + domain) for domain in DOMAINS):
            return True
    return False


def intermediary_posting(job: JobPosting) -> bool:
    """Check both the source's publisher and its observed outbound URLs.

    Args:
        job: Source posting.

    Returns:
        Whether the source is an intermediary.
    """
    return intermediary_values(job.company, job.url, job.apply_url)


def trusted_ats_url(url: str) -> bool:
    """Require both a recognized ATS host and a concrete requisition URL.

    Args:
        url: Candidate official job page.

    Returns:
        Whether the URL belongs to a supported ATS with a parsed posting ID.
    """
    try:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").lower()
        port = parsed.port
    except ValueError:
        return False
    hosts = (
        "boards.greenhouse.io",
        "job-boards.greenhouse.io",
        "boards.eu.greenhouse.io",
        "jobs.lever.co",
        "jobs.ashbyhq.com",
        "careers.southwestair.com",
    )
    return bool(
        parsed.scheme == "https"
        and not parsed.username
        and not parsed.password
        and port in (None, 443)
        and (host in hosts or host.endswith((".myworkdayjobs.com", ".eightfold.ai")))
        and observed_identifier(url)
    )


def matches_official(source: JobPosting, target: JobPosting) -> bool:
    """Require employer evidence, compatible location and near-identical long JD.

    Args:
        source: Intermediary posting retaining its original description.
        target: Official ATS candidate fetched or stored independently.

    Returns:
        True for strong attribution evidence; callers must reject ambiguity.
    """
    if (
        not trusted_ats_url(target.url)
        or intermediary_posting(target)
        or target.closed_at
    ):
        return False
    if target.jd_quality not in (QualityBand.GOOD, QualityBand.FULL):
        return False
    target_identity = observed_identifier(target.url)
    assert target_identity is not None
    for url in (source.url, source.apply_url, source.identity_evidence_url):
        identity = observed_identifier(url or "")
        if identity is not None and (identity.provider, identity.scope) == (
            target_identity.provider,
            target_identity.scope,
        ):
            return identity.native_id == target_identity.native_id
    if normalize(source.title) != normalize(target.title):
        return False
    company = normalize_company(target.company)
    evidence = normalize(source.jd_text or "")
    if not company or (
        normalize_company(source.company) != company
        and f" {company} " not in f" {evidence} "
    ):
        return False
    broad = {"", "unknown", "remote", "united states", "usa", "us", "nationwide"}
    locations = [
        normalize(j.location).removesuffix(" united states").strip()
        for j in (source, target)
    ]
    if (
        locations[0] != locations[1]
        and all(loc not in broad for loc in locations)
        and not (
            set(locations[0].split()) <= set(locations[1].split())
            or set(locations[1].split()) <= set(locations[0].split())
        )
    ):
        return False
    left, right = [
        re.findall(r"\w+", (j.jd_text or "").casefold()) for j in (source, target)
    ]
    if (
        min(len(left), len(right)) < _MIN_WORDS
        or max(len(left), len(right)) > _MAX_WORDS
    ):
        return False
    if min(len(set(left)), len(set(right))) < _MIN_DISTINCT_WORDS:
        return False
    comparison = SequenceMatcher(None, left, right, autojunk=False)
    matched = sum(block.size for block in comparison.get_matching_blocks())
    return comparison.ratio() >= _MIN_SIMILARITY or (
        matched / len(left) >= _MIN_SOURCE_COVERAGE
        and matched / len(right) >= _MIN_OFFICIAL_COVERAGE
    )
