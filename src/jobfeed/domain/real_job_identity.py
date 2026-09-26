"""Conservative evidence rules for joining source postings into real jobs."""

from __future__ import annotations

import re

from jobfeed.domain.external_identity import ObservedIdentifier, observed_identifier
from jobfeed.domain.models import JobPosting
from jobfeed.domain.normalize import normalize, normalize_company

_MIN_SUBSTANTIVE_CHARS = 300
_BROAD_LOCATIONS = frozenset(
    {
        "",
        "unknown",
        "remote",
        "united states",
        "usa",
        "us",
        "nationwide",
    }
)
_ROLE_HEADING = re.compile(r"(?im)^\s*role\s*$")
ATS_REQUISITION_PROVIDERS = frozenset(
    {"greenhouse", "ashby", "lever", "workday", "phenom", "eightfold"}
)


def observed_identifiers(job: JobPosting) -> tuple[ObservedIdentifier, ...]:
    """Use only URLs observed on this source, never a claimed ID alone.

    Args:
        job: Source posting with its listing, Apply, and identity evidence URLs.

    Returns:
        Parsed provider-scoped identifiers in observed URL order.
    """
    urls = dict.fromkeys(
        url for url in (job.url, job.apply_url, job.identity_evidence_url) if url
    )
    identifiers = [
        identifier
        for url in urls
        if (identifier := observed_identifier(url)) is not None
    ]
    return tuple(identifiers)


def source_identifiers(job: JobPosting) -> tuple[ObservedIdentifier, ...]:
    """Include the ingestion source's native posting ID as observed evidence.

    Args:
        job: Source posting and its observed external URLs.

    Returns:
        Distinct URL-derived identifiers plus the source-native identifier.
    """
    identifiers = observed_identifiers(job)
    source = ObservedIdentifier(job.platform, "", job.canonical_id, job.url)
    return tuple(
        {
            (item.provider, item.scope, item.native_id): item
            for item in (*identifiers, source)
        }.values()
    )


def compatible_role_facts(left: JobPosting, right: JobPosting) -> bool:
    """Require employer/title agreement and no concrete location conflict.

    Args:
        left: First source posting to compare.
        right: Other source posting to compare.

    Returns:
        Whether the two postings can describe the same requisition.
    """
    company = normalize_company(left.company)
    title = normalize(left.title)
    if not company or not title:
        return False
    if title != normalize(right.title):
        return False
    left_observed = observed_identifiers(left)
    right_observed = observed_identifiers(right)
    if company != normalize_company(right.company) and not _verified_ats_alias(
        left, right, left_observed, right_observed
    ):
        return False
    locations = (_concrete_location(left.location), _concrete_location(right.location))
    if locations[0] != locations[1] and not any(
        location in _BROAD_LOCATIONS for location in locations
    ):
        return False
    left_ids = {(i.provider, i.scope): i.native_id for i in left_observed}
    right_ids = {(i.provider, i.scope): i.native_id for i in right_observed}
    return not any(
        left_ids[key] != right_ids[key] for key in left_ids.keys() & right_ids.keys()
    )


def _concrete_location(value: str) -> str:
    location = normalize(value)
    return location.removesuffix(" united states").strip() or location


def _verified_ats_alias(
    left: JobPosting,
    right: JobPosting,
    left_observed: tuple[ObservedIdentifier, ...],
    right_observed: tuple[ObservedIdentifier, ...],
) -> bool:
    shared = {(i.provider, i.scope, i.native_id) for i in left_observed} & {
        (i.provider, i.scope, i.native_id) for i in right_observed
    }
    if not any(provider in ATS_REQUISITION_PROVIDERS for provider, _, _ in shared):
        return False
    left_body = normalized_jd_body(left.jd_text)
    return len(left_body) >= _MIN_SUBSTANTIVE_CHARS and left_body == normalized_jd_body(
        right.jd_text
    )


def strict_content_equivalent(left: JobPosting, right: JobPosting) -> bool:
    """Match long substantive text exactly after harmless layout normalization.

    Args:
        left: First posting with a potentially complete description.
        right: Other posting with a potentially complete description.

    Returns:
        Whether compatible facts and normalized long descriptions agree.
    """
    if not compatible_role_facts(left, right):
        return False
    left_body = normalized_jd_body(left.jd_text)
    right_body = normalized_jd_body(right.jd_text)
    return len(left_body) >= _MIN_SUBSTANTIVE_CHARS and left_body == right_body


def normalized_jd_body(value: str | None) -> str:
    """Remove a standalone Role heading and layout from JD comparison text.

    Args:
        value: Source description, or none when it is unavailable.

    Returns:
        Case-folded alphanumeric text used for exact content comparison.
    """
    without_heading = _ROLE_HEADING.sub("", value or "")
    return "".join(
        character for character in without_heading.casefold() if character.isalnum()
    )
