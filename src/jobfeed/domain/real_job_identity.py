"""Conservative evidence rules for joining source postings into real jobs."""

from __future__ import annotations

import re

from jobfeed.domain.external_identity import ObservedIdentifier, observed_identifier
from jobfeed.domain.models import JobPosting
from jobfeed.domain.normalize import normalize, normalize_company
from jobfeed.domain.requisition_alias import Aliases, canonical_identifier

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
    {
        "greenhouse",
        "ashby",
        "lever",
        "workday",
        "phenom",
        "eightfold",
        "avature",
        "smartrecruiters",
        "talentbrew",
    }
)


def observed_identifiers(
    job: JobPosting, aliases: Aliases | None = None
) -> tuple[ObservedIdentifier, ...]:
    """Use only URLs observed on this source, never a claimed ID alone.

    Args:
        job: Source posting with its listing, Apply, and identity evidence URLs.
        aliases: Optional verified Workday aliases.

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
    return tuple(canonical_identifier(i, aliases or {}) for i in identifiers)


def source_identifiers(
    job: JobPosting, aliases: Aliases | None = None
) -> tuple[ObservedIdentifier, ...]:
    """Include the ingestion source's native posting ID as observed evidence.

    Args:
        job: Source posting and its observed external URLs.
        aliases: Optional verified Workday aliases.

    Returns:
        Distinct URL-derived identifiers plus the source-native identifier.
    """
    identifiers = observed_identifiers(job, aliases)
    source = ObservedIdentifier(job.platform, "", job.canonical_id, job.url)
    return tuple(
        {
            (item.provider, item.scope, item.native_id): item
            for item in (*identifiers, source)
        }.values()
    )


def compatible_role_facts(  # noqa: C901 - explicit contradictory identity guard
    left: JobPosting, right: JobPosting, aliases: Aliases | None = None
) -> bool:
    """Prefer official requisition identity over source display metadata.

    Time complexity: O(n) over the bounded observed URL identities.

    Args:
        left: First source posting to compare.
        right: Other source posting to compare.
        aliases: Optional verified Workday aliases.

    Returns:
        Whether the two postings can describe the same requisition.
    """
    left_observed = observed_identifiers(left, aliases)
    right_observed = observed_identifiers(right, aliases)
    left_ats = {
        (i.provider, i.scope): i.native_id
        for i in left_observed
        if i.provider in ATS_REQUISITION_PROVIDERS
    }
    right_ats = {
        (i.provider, i.scope): i.native_id
        for i in right_observed
        if i.provider in ATS_REQUISITION_PROVIDERS
    }
    for items in (left_observed, right_observed):
        scoped: dict[tuple[str, str], set[str]] = {}
        for item in items:
            if item.provider in ATS_REQUISITION_PROVIDERS:
                scoped.setdefault((item.provider, item.scope), set()).add(
                    item.native_id
                )
        if any(len(values) > 1 for values in scoped.values()):
            return False
    shared_ats = left_ats.keys() & right_ats.keys()
    shared_providers = {p for p, _ in left_ats} & {p for p, _ in right_ats}
    if shared_providers - {p for p, _ in shared_ats}:
        return False
    if any(left_ats[key] != right_ats[key] for key in shared_ats):
        return False
    if shared_ats:
        return True
    company = normalize_company(left.company)
    title = normalize(left.title)
    if not company or not title:
        return False
    if title != normalize(right.title):
        return False
    if company != normalize_company(right.company) and not _verified_ats_alias(
        left, right, left_observed, right_observed
    ):
        return False
    # One concrete source posting can display different city labels on mirrors.
    # Official requisition conflicts above still take precedence.
    shared_postings = {(i.provider, i.scope, i.native_id) for i in left_observed} & {
        (i.provider, i.scope, i.native_id) for i in right_observed
    }
    if any(p == "linkedin" for p, _, _ in shared_postings):
        return True
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


def strict_content_equivalent(
    left: JobPosting, right: JobPosting, aliases: Aliases | None = None
) -> bool:
    """Match long substantive text exactly after harmless layout normalization.

    Args:
        left: First posting with a potentially complete description.
        right: Other posting with a potentially complete description.
        aliases: Optional verified Workday aliases.

    Returns:
        Whether compatible facts and normalized long descriptions agree.
    """
    if not compatible_role_facts(left, right, aliases):
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
