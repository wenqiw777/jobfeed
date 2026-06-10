"""Pure parsers that seed ATS company slugs from public aggregator READMEs.

Public GitHub job-list repos (SimplifyJobs, speedyapply, vanshb03, pittcsc,
cvrve) embed ATS apply URLs in their README job tables. The slug regexes are
generic, so any markdown that embeds boards.greenhouse.io / jobs.ashbyhq.com /
jobs.lever.co URLs works regardless of repo structure.

This module is pure (regex + stdlib only): no HTTP and no store access — the
``bootstrap-companies`` CLI command owns all IO. Behavioral parity with the
legacy ``_extract_ats_slugs`` / ``_extract_ats_slugs_with_age`` helpers.
"""

from __future__ import annotations

import re
from collections.abc import Iterator

# Public READMEs we scrape for ATS slugs. Adding a source = one new entry
# here; the CLI's --source choices are derived from these keys.
BOOTSTRAP_SOURCES: dict[str, str] = {
    "simplifyjobs-newgrad": (
        "https://raw.githubusercontent.com/SimplifyJobs/"
        "New-Grad-Positions/master/README.md"
    ),
    "simplifyjobs-summer-internships": (
        "https://raw.githubusercontent.com/SimplifyJobs/"
        "Summer2026-Internships/master/README.md"
    ),
    "speedyapply-swe": (
        "https://raw.githubusercontent.com/speedyapply/"
        "2026-SWE-College-Jobs/master/README.md"
    ),
    "vanshb03-summer": (
        "https://raw.githubusercontent.com/vanshb03/"
        "Summer2026-Internships/master/README.md"
    ),
    # pittcsc is the original Summer-Internships repo that SimplifyJobs
    # forked; the two have diverged enough that pittcsc adds slugs the
    # SimplifyJobs fork has dropped or never picked up.
    "pittcsc-summer": (
        "https://raw.githubusercontent.com/pittcsc/Summer2026-Internships/dev/README.md"
    ),
    # cvrve maintains a rolling new-grad list (separate from cycle-tagged
    # internship repos) — different curation philosophy than SimplifyJobs
    # new-grad, so the overlap isn't total.
    "cvrve-newgrad": (
        "https://raw.githubusercontent.com/cvrve/New-Grad/main/README.md"
    ),
    "vanshb03-newgrad": (
        "https://raw.githubusercontent.com/vanshb03/New-Grad-2026/main/README.md"
    ),
}

# (regex, vendor) pairs ported verbatim from the legacy CLI.
ATS_SLUG_PATTERNS: tuple[tuple[str, str], ...] = (
    # job-boards.greenhouse.io and boards.eu.greenhouse.io are alt mirrors.
    (
        r"(?:job-)?boards(?:\.eu)?\.greenhouse\.io/([a-zA-Z0-9][a-zA-Z0-9_-]*)",
        "greenhouse",
    ),
    (r"jobs\.ashbyhq\.com/([a-zA-Z0-9][a-zA-Z0-9_-]*)", "ashby"),
    (r"jobs\.lever\.co/([a-zA-Z0-9][a-zA-Z0-9_-]*)", "lever"),
)

# Path components that look like slugs to the regex but are actually ATS
# infrastructure paths (404 on the boards API). `embed` — Greenhouse's
# `boards.greenhouse.io/embed/job_app?for=<real>` widget URL captures
# "embed" instead of the real slug.
_NON_COMPANY_SLUGS: dict[str, frozenset[str]] = {
    "greenhouse": frozenset({"embed"}),
    "ashby": frozenset(),
    "lever": frozenset(),
}

# Aggregator README job tables render as HTML `<tr>` rows; the age cell uses
# two units: `Nd` (days) and `Nmo` (months, converted at 30 days/month).
_AGE_RE = re.compile(r"<td[^>]*>\s*(\d+)\s*(d|mo)\s*</td>")
_DAYS_PER_MONTH = 30
_ROW_CLOSE = "</tr>"
_ROW_OPEN = "<tr"


def extract_ats_slugs(markdown: str) -> set[tuple[str, str]]:
    """Pull unique ``(slug, vendor)`` pairs out of a markdown blob.

    Slugs are lowercased and deduped; known non-company infrastructure path
    components (e.g. greenhouse ``embed``) are dropped.

    Args:
        markdown: Raw README markdown/HTML text.

    Returns:
        Deduplicated ``(slug, vendor)`` pairs.

    Complexity:
        O(len(markdown) * len(ATS_SLUG_PATTERNS)) — one regex scan per vendor.
    """
    found: set[tuple[str, str]] = set()
    for pattern, vendor in ATS_SLUG_PATTERNS:
        slugs = {m.group(1).lower() for m in re.finditer(pattern, markdown)}
        found |= {(slug, vendor) for slug in slugs - _NON_COMPANY_SLUGS[vendor]}
    return found


def extract_ats_slugs_with_age(
    markdown: str, max_age_days: int
) -> set[tuple[str, str]]:
    """Age-filtered variant of :func:`extract_ats_slugs` over ``<tr>`` rows.

    A row's slugs are included when its age cell parses to at most
    ``max_age_days``; rows with no parseable age cell pass through. ATS URLs
    outside ``<tr>`` rows are ignored (legacy parity: only table rows carry
    an age column).

    Args:
        markdown: Raw README markdown/HTML text.
        max_age_days: Inclusive upper bound on the row age, in days.

    Returns:
        Deduplicated ``(slug, vendor)`` pairs from young-enough rows.

    Complexity:
        O(len(markdown) * len(ATS_SLUG_PATTERNS)) — the row scan is linear
        and each row body is scanned once per vendor pattern.
    """
    found: set[tuple[str, str]] = set()
    for body in _iter_row_bodies(markdown):
        age_days = _parse_age_days(body)
        if age_days is not None and age_days > max_age_days:
            continue
        found |= extract_ats_slugs(body)
    return found


def _iter_row_bodies(markdown: str) -> Iterator[str]:
    """Yield the body of each ``<tr ...>body</tr>`` row in document order.

    Splits on the literal ``</tr>`` close tag and, per chunk, takes the text
    after the last ``<tr ...>`` open tag as the row body. Text outside any
    row (no enclosing ``<tr``/``</tr>`` pair) is never yielded. On
    well-formed tables this is equivalent to the previous
    ``<tr[^>]*>(.*?)</tr>`` regex scan, which backtracked quadratically on
    documents with many unclosed ``<tr>`` tags.

    Args:
        markdown: Raw README markdown/HTML text.

    Yields:
        Row body text between an open ``<tr ...>`` tag and its ``</tr>``.

    Complexity:
        O(len(markdown)) — the split visits each character once, and each
        chunk is scanned a constant number of times (rfind + find).
    """
    # The text after the final close tag has no row terminator: drop it.
    for chunk in markdown.split(_ROW_CLOSE)[:-1]:
        open_at = chunk.rfind(_ROW_OPEN)
        if open_at == -1:
            continue  # no open tag before this close tag: not a row
        body_at = chunk.find(">", open_at)
        if body_at == -1:
            continue  # open tag never closed by '>': not a row
        yield chunk[body_at + 1 :]


def _parse_age_days(row_body: str) -> int | None:
    """Parse a row's age cell to days; None when absent or unparseable."""
    match = _AGE_RE.search(row_body)
    if match is None:
        return None
    count, unit = int(match.group(1)), match.group(2)
    return count * _DAYS_PER_MONTH if unit == "mo" else count


__all__ = [
    "ATS_SLUG_PATTERNS",
    "BOOTSTRAP_SOURCES",
    "extract_ats_slugs",
    "extract_ats_slugs_with_age",
]
