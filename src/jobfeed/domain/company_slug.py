"""Pure normalization of pasted company entries into ATS slug candidates.

The probe route accepts free-form pasted entries: plain slugs ("Acme") or
vendor board URLs ("https://boards.greenhouse.io/acme"). This module reduces
each entry to a lowercase candidate slug, or an error string explaining why
the entry cannot be normalized.

The URL host shapes mirror the aggregator-bootstrap parser
(``adapters/sources/_bootstrap_aggregators.py``); domain stays import-pure,
so that host knowledge is restated here as plain regex strings.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Slug charset the vendor boards accept: alphanumeric start, then
# alphanumerics, underscores, or hyphens.
_SLUG_RE = re.compile(r"[a-z0-9][a-z0-9_-]*")

# URL patterns match against lowercased text, so lowercase classes suffice.
# (job-)boards(.eu).greenhouse.io are alternate Greenhouse mirrors.
_URL_SLUG_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?:job-)?boards(?:\.eu)?\.greenhouse\.io/([a-z0-9][a-z0-9_-]*)"),
    re.compile(r"jobs\.ashbyhq\.com/([a-z0-9][a-z0-9_-]*)"),
    re.compile(r"jobs\.lever\.co/([a-z0-9][a-z0-9_-]*)"),
)

# Greenhouse embed-widget URLs (`boards.greenhouse.io/embed/job_app?for=x`)
# carry the real slug in the `for` query parameter; the path component the
# generic pattern would capture is the literal "embed".
_GREENHOUSE_EMBED_MARKER = "greenhouse.io/embed"
_GREENHOUSE_EMBED_FOR_RE = re.compile(r"[?&]for=([a-z0-9][a-z0-9_-]*)")


@dataclass(frozen=True, kw_only=True)
class SlugCandidate:
    """Outcome of normalizing one pasted entry: a slug or an error, never both."""

    slug: str | None = None
    error: str | None = None


def normalize_slug(value: str) -> str:
    """Trim and lowercase a raw slug (no validity check).

    Args:
        value: Raw slug text.

    Returns:
        Whitespace-trimmed, lowercased slug candidate.
    """
    return value.strip().lower()


def is_valid_slug(value: str) -> bool:
    """Report whether normalized text is a slug the vendor boards accept.

    Args:
        value: Normalized (trimmed, lowercase) slug candidate.

    Returns:
        True when the whole text matches the slug charset.
    """
    return _SLUG_RE.fullmatch(value) is not None


def normalize_company_entry(entry: str) -> SlugCandidate:
    """Normalize a pasted entry (slug or board URL) to a candidate slug.

    Plain entries are trimmed and lowercased; entries containing a path
    separator are treated as URLs and reduced to the slug of a known
    greenhouse / ashby / lever board URL.

    Args:
        entry: Raw pasted text.

    Returns:
        Candidate with ``slug`` set on success, or ``error`` describing why
        the entry cannot be normalized.
    """
    text = entry.strip()
    if not text:
        return SlugCandidate(error="empty entry")
    if "/" in text:
        return _slug_from_url(text)
    lowered = text.lower()
    if not is_valid_slug(lowered):
        return SlugCandidate(error=f"not a valid company slug: {text!r}")
    return SlugCandidate(slug=lowered)


def _slug_from_url(text: str) -> SlugCandidate:
    """Extract the board slug from a known ATS board URL.

    Matching runs over the lowercased text so uppercase hosts and query
    parameters (``?FOR=``) still resolve; captures are already lowercase.

    Args:
        text: Trimmed URL-shaped entry (contains a path separator).

    Returns:
        Candidate with the lowercased slug, or an error for unknown hosts
        and slug-less board URLs.
    """
    lowered = text.lower()
    if _GREENHOUSE_EMBED_MARKER in lowered:
        embed = _GREENHOUSE_EMBED_FOR_RE.search(lowered)
        if embed is None:
            return SlugCandidate(error=f"embed URL carries no for= slug: {text!r}")
        return SlugCandidate(slug=embed.group(1))
    for pattern in _URL_SLUG_PATTERNS:
        match = pattern.search(lowered)
        if match is not None:
            return SlugCandidate(slug=match.group(1))
    return SlugCandidate(error=f"no ATS board slug found in URL: {text!r}")


__all__ = [
    "SlugCandidate",
    "is_valid_slug",
    "normalize_company_entry",
    "normalize_slug",
]
