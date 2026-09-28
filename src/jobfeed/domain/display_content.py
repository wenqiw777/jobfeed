"""Conservative display equivalence; never use for enrichment or write identity."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from difflib import SequenceMatcher
from html import unescape
from unicodedata import normalize as unicode_normalize

from jobfeed.domain.models import JobPosting
from jobfeed.domain.normalize import normalize, normalize_company

_MIN_BODY_LENGTH = 200
_MIN_LOCATION_LENGTH = 3
_MAX_LAYOUT_HEADING_WORDS = 5
_NEAR_COPY_RATIO = 0.995
_UNKNOWN = frozenset({"", "unknown", "unknown company"})
_REQUIREMENT = re.compile(
    r"\b(?:qualifications?|requirements?|required|must|skills?|experience|"
    r"eligible|eligibility|citizenship|clearance|authorization|sponsorship|"
    r"teams?|departments?|responsibilities|duties|senior|junior|level)\b",
    re.IGNORECASE,
)
_HARD_REQUIREMENT = re.compile(
    r"\b(?:required|must|minimum|qualifications?|experience|skills?|"
    r"teams?|departments?|responsibilities|duties|level)\b",
    re.IGNORECASE,
)


def _text(value: str) -> str:
    return " ".join(
        re.findall(
            r"\w+|[+#<>=]", unicode_normalize("NFKC", unescape(value)).casefold()
        )
    )


def _locations(left: JobPosting, right: JobPosting) -> list[str]:
    """Time complexity: O(L * T), for location entries L and their text length T."""
    locations: set[str] = set()
    for job in (left, right):
        for location in (job.location or "").split(";"):
            # Match full city/state text or a multi-character city, never a
            # two-letter state independently (IN/OR/ME are ordinary JD words).
            for part in (location, location.split(",")[0]):
                token = _text(part)
                if len(token) >= _MIN_LOCATION_LENGTH and token not in {
                    "remote",
                    "united states",
                    "usa",
                }:
                    locations.add(token)
    return sorted(locations, key=len, reverse=True)


def _without_locations(value: str, locations: list[str]) -> str:
    return _strip_locations(_text(value), locations)


def _strip_locations(value: str, locations: list[str]) -> str:
    if not any(location in value for location in locations):
        return value
    result = f" {value} "
    for location in locations:
        # Normalized text is token-separated, so an absent substring cannot match.
        if location in result:
            result = re.sub(
                rf"(?<!\w){re.escape(location)}(?!\w)", " location ", result
            )
    return " ".join(result.split())


def _requirements(value: str, locations: list[str]) -> tuple[str, ...]:
    # Preserve entire qualification sections, including heading-following lists.
    # Elsewhere retain sentences mentioning requirements and all numeric tokens.
    heading = re.search(
        r"(?:basic|preferred|minimum)\s+qualifications|(?:job\s+)?requirements\s*:",
        value,
        re.IGNORECASE,
    )
    protected = [value[heading.start() :]] if heading else []
    protected.extend(
        sentence
        for sentence in re.split(r"[.!?\n]+", value)
        if _REQUIREMENT.search(sentence)
    )
    return tuple(_without_locations(part, locations) for part in protected)


def same_display_content(left: JobPosting, right: JobPosting) -> bool:
    """Accept long near-copies only within a matching title/employer boundary.

    Args:
        left: First posting to compare.
        right: Second posting to compare.

    Returns:
        Whether the two postings can be folded for display.
    """
    return _same_prepared(_Prepared(left), _Prepared(right))


@dataclass
class _Prepared:
    job: JobPosting
    body: str | None = None

    def normalized_body(self) -> str:
        if self.body is None:
            self.body = _text(self.job.jd_text or "")
        return self.body


def _same_prepared(left_data: _Prepared, right_data: _Prepared) -> bool:
    left, right = left_data.job, right_data.job
    title = normalize(left.title)
    if not title or title != normalize(right.title):
        return False
    companies = {normalize_company(job.company) for job in (left, right)} - _UNKNOWN
    if len(companies) > 1:
        primary = {
            normalize_company(job.company.split("+", 1)[0].strip())
            for job in (left, right)
        }
        if len(primary) > 1 or not any("+" in job.company for job in (left, right)):
            return False
    if not left.jd_text or not right.jd_text:
        return False
    locations = _locations(left, right)
    a = _strip_locations(left_data.normalized_body(), locations)
    b = _strip_locations(right_data.normalized_body(), locations)
    if min(len(a), len(b)) < _MIN_BODY_LENGTH:
        return False
    if a == b:
        return True
    # A tiny percentage can still change experience, salary, negation or a
    # hard requirement. Those changes do not qualify as cosmetic near-copies.
    if re.findall(r"\d+", a) != re.findall(r"\d+", b):
        return False
    if re.findall(r"\b(?:no|not|without|must)\b", a) != re.findall(
        r"\b(?:no|not|without|must)\b", b
    ):
        return False
    if not _requirements_equivalent(left, right, locations, a, b):
        return False
    matcher = SequenceMatcher(None, a.split(), b.split(), autojunk=False)
    return (
        matcher.quick_ratio() >= _NEAR_COPY_RATIO
        and matcher.ratio() >= _NEAR_COPY_RATIO
    )


def _requirements_equivalent(
    left: JobPosting,
    right: JobPosting,
    locations: list[str],
    left_body: str,
    right_body: str,
) -> bool:
    """Allow heading layout differences, but reject new requirement words."""
    if _hard_requirement_words(left.jd_text or "") != _hard_requirement_words(
        right.jd_text or ""
    ):
        return False
    left_words = Counter(" ".join(_requirements(left.jd_text or "", locations)).split())
    right_words = Counter(
        " ".join(_requirements(right.jd_text or "", locations)).split()
    )
    if left_words == right_words:
        return True
    left_tokens = set(left_body.split())
    right_tokens = set(right_body.split())
    return all(word in right_tokens for word in left_words - right_words) and all(
        word in left_tokens for word in right_words - left_words
    )


def _hard_requirement_words(body: str) -> Counter[str]:
    """Preserve tokens in explicit requirement and team statements."""
    return Counter(
        word
        for sentence in re.split(r"[.!?\n]+", body)
        for cleaned in [_strip_layout_heading(sentence)]
        if _HARD_REQUIREMENT.search(cleaned)
        for word in _text(cleaned).split()
    )


def _strip_layout_heading(sentence: str) -> str:
    """Ignore short non-requirement labels joined to a bullet by formatting."""
    prefix, separator, rest = sentence.partition(":")
    if (
        separator
        and len(prefix.split()) <= _MAX_LAYOUT_HEADING_WORDS
        and not _HARD_REQUIREMENT.search(prefix)
    ):
        return rest
    return sentence


def fold_content_groups(groups: list[list[JobPosting]]) -> list[list[JobPosting]]:
    """Fold native groups using all anchors, preventing transitive similarity chains.

    Args:
        groups: Groups supplied by the caller.

    Returns:
        Groups of display-equivalent postings.

    Time complexity: O(N² * T), for postings N and comparison text length T.
    """
    buckets: dict[str, list[list[JobPosting]]] = {}
    result: list[list[JobPosting]] = []
    prepared = {id(job): _Prepared(job) for members in groups for job in members}
    employers = {
        id(job): normalize_company(job.company) for members in groups for job in members
    }
    for members in groups:
        anchor = max(members, key=lambda job: len(job.jd_text or ""))
        title = normalize(anchor.title)
        candidates = buckets.setdefault(title, [])
        for group in candidates:
            company = employers[id(anchor)]
            if company not in _UNKNOWN and any(
                employers[id(other)] not in _UNKNOWN and employers[id(other)] != company
                for other in group
            ):
                continue
            if all(
                _same_prepared(prepared[id(anchor)], prepared[id(other)])
                for other in group
            ):
                group.extend(members)
                break
        else:
            group = list(members)
            candidates.append(group)
            result.append(group)
    return result
