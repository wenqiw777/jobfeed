"""HTML parsers for the LinkedIn guest (anonymous) job endpoints.

The guest search endpoint (``seeMoreJobPostings/search``) returns a flat HTML
fragment of ``div.base-search-card`` elements; the posting endpoint
(``jobPosting/{id}``) returns a detail fragment with the JD body in
``div.show-more-less-html__markup`` and a relative posted-time marker
(``posted-time-ago__text``, e.g. ``"2 weeks ago"``).

This module is pure: it never makes HTTP calls and imports only stdlib + bs4.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

from bs4 import BeautifulSoup, Tag

VIEW_URL = "https://www.linkedin.com/jobs/view/{job_id}"

# Relative posted-time text, e.g. "3 days ago", "1 hour ago", "Reposted 2
# weeks ago". Weeks/months are approximated in days below. "Just now" /
# "Just posted" mark the freshest postings and map to exactly ``now``.
_RELATIVE_RE = re.compile(r"(\d+)\s+(second|minute|hour|day|week|month)s?\s+ago", re.I)
_JUST_NOW_RE = re.compile(r"just\s+(now|posted)", re.I)
_DAYS_PER = {"day": 1, "week": 7, "month": 30}


@dataclass(frozen=True, kw_only=True)
class ParsedCard:
    """One parsed guest search card (pre-JD-enrichment)."""

    job_id: str
    title: str
    company: str
    location: str | None
    posted_at: datetime | None
    url: str


def parse_search_cards(html: str) -> list[ParsedCard]:
    """Parse every guest search card from a search-results HTML fragment.

    Cards missing a numeric job id, a title, or a company are skipped
    (downstream JobPosting construction needs all three).

    Args:
        html: Raw HTML fragment from the guest search endpoint.

    Returns:
        Parsed cards in document order.
    """
    soup = BeautifulSoup(html, "html.parser")
    cards = []
    for div in soup.select("div.base-search-card"):
        card = _parse_card(div)
        if card is not None:
            cards.append(card)
    return cards


def count_search_cards(html: str) -> int:
    """Count raw ``div.base-search-card`` elements, before validity filtering.

    LinkedIn's ``start`` offset is positional over its RAW result set, so
    pagination must advance by this count — not by how many cards survived
    ``parse_search_cards``'s id/title/company filtering (promoted, malformed,
    or layout-drifted cards are skipped there but still occupy offsets).

    Args:
        html: Raw HTML fragment from the guest search endpoint.

    Returns:
        Number of card divs in the fragment, valid or not.
    """
    soup = BeautifulSoup(html, "html.parser")
    return len(soup.select("div.base-search-card"))


def parse_jd(html: str) -> str:
    """Extract the JD body text from a guest posting HTML fragment.

    Args:
        html: Raw HTML fragment from the guest posting endpoint.

    Returns:
        Cleaned JD text with newline separators (preserving paragraph/list
        structure for LLM prompts, like the ATS sources' html_to_text), or
        ``""`` when the markup element is absent.
    """
    soup = BeautifulSoup(html, "html.parser")
    markup = soup.select_one("div.show-more-less-html__markup")
    if markup is None:
        return ""
    return markup.get_text("\n", strip=True)


def parse_posting_posted_at(html: str, *, now: datetime) -> datetime | None:
    """Read the posting fragment's relative posted-time marker.

    With ``f_TPR`` set, search cards may omit the date, so the posting
    fragment's ``posted-time-ago__text`` (e.g. ``"2 weeks ago"``) is the
    enrich-time fallback. The delta is subtracted from the injected ``now``
    (seconds/minutes/hours/days; weeks as 7 days; months as 30 days);
    ``"Just now"`` / ``"Just posted"`` map to exactly ``now``.

    Args:
        html: Raw HTML fragment from the guest posting endpoint.
        now: Aware-UTC reference time the relative delta is anchored to.

    Returns:
        Aware-UTC posted-at estimate, or None when the marker is absent or
        its text is unparseable.
    """
    soup = BeautifulSoup(html, "html.parser")
    marker = soup.select_one(".posted-time-ago__text")
    if marker is None:
        return None
    return _parse_relative(marker.get_text(" ", strip=True), now=now)


def _parse_card(div: Tag) -> ParsedCard | None:
    """Map one ``base-search-card`` div to a ParsedCard, or None to skip."""
    job_id = _extract_job_id(div)
    title = _text_of(div.select_one("h3"))
    company = _text_of(div.select_one("h4"))
    if job_id is None or not title or not company:
        return None
    return ParsedCard(
        job_id=job_id,
        title=title,
        company=company,
        location=_text_of(div.select_one(".job-search-card__location")) or None,
        posted_at=_parse_card_posted_at(div),
        url=VIEW_URL.format(job_id=job_id),
    )


def _extract_job_id(div: Tag) -> str | None:
    """Pull the bare numeric job id from the card's full-link href.

    The href path ends in a slug like ``senior-engineer-at-acme-4012345678``;
    the id is the last ``-``-delimited segment of the final path component
    (query string stripped first). Returns None unless that segment is all
    digits.
    """
    link = div.select_one("a.base-card__full-link")
    if link is None:
        return None
    path = urlsplit(str(link.get("href") or "")).path.rstrip("/")
    segment = path.rsplit("/", 1)[-1].rsplit("-", 1)[-1]
    return segment if segment.isdigit() else None


def _parse_card_posted_at(div: Tag) -> datetime | None:
    """Parse the card's ``<time datetime=...>`` attr to an aware-UTC datetime.

    A date-only attr becomes midnight UTC; a missing tag, blank attr, or
    non-ISO value yields None.
    """
    time_el = div.select_one("time")
    if time_el is None:
        return None
    raw = str(time_el.get("datetime") or "").strip()
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _parse_relative(text: str, *, now: datetime) -> datetime | None:
    """Convert relative posted-time text to ``now`` minus the parsed delta."""
    if _JUST_NOW_RE.search(text):
        return now
    match = _RELATIVE_RE.search(text)
    if match is None:
        return None
    count, unit = int(match.group(1)), match.group(2).lower()
    if unit == "second":
        return now - timedelta(seconds=count)
    if unit == "minute":
        return now - timedelta(minutes=count)
    if unit == "hour":
        return now - timedelta(hours=count)
    return now - timedelta(days=count * _DAYS_PER[unit])


def _text_of(element: Tag | None) -> str:
    """Cleaned text of an element, or ``""`` when the element is absent."""
    if element is None:
        return ""
    return element.get_text(" ", strip=True)


__all__ = [
    "VIEW_URL",
    "ParsedCard",
    "count_search_cards",
    "parse_jd",
    "parse_posting_posted_at",
    "parse_search_cards",
]
