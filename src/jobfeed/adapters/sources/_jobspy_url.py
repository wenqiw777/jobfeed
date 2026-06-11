"""Search-URL -> JobSpy kwargs parsing (pure stdlib).

Split out of ``_jobspy.py`` to keep that module under the 300-line gate. This
module touches NO jobspy/pandas types — it only turns a search URL's query
string into the keyword arguments ``jobspy.scrape_jobs`` accepts. Only the
Indeed mapping remains (the LinkedIn-via-JobSpy path was replaced by the
in-repo ``linkedin_guest`` source).

Key map::

    Indeed   (legacy indeed.py:109-135):  q -> search_term, l -> location,
             fromage=N -> hours_old=N*24, radius -> distance
"""

from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs, urlparse


def parse_search_url(url: str) -> dict[str, Any]:
    """Translate an Indeed search URL into JobSpy kwargs.

    Unrecognized params are dropped (JobSpy supplies its own defaults).

    Args:
        url: User-pasted search URL.

    Returns:
        JobSpy keyword arguments parsed from the URL's query string.
    """
    qs = parse_qs(urlparse(url).query)
    return _parse_indeed_qs(qs)


def _parse_indeed_qs(qs: dict[str, list[str]]) -> dict[str, Any]:
    """Map Indeed query params: q/l/fromage/radius (legacy indeed.py:109-135)."""
    out: dict[str, Any] = {}
    if term := _first(qs, "q"):
        out["search_term"] = term
    if location := _first(qs, "l"):
        out["location"] = location
    if (fromage := _first(qs, "fromage")) and (hours := _to_hours_from_days(fromage)):
        out["hours_old"] = hours
    if (radius := _first(qs, "radius")) and (distance := _to_int(radius)):
        out["distance"] = distance
    return out


def _first(qs: dict[str, list[str]], key: str) -> str | None:
    """Return the first stripped value for ``key``, or None if blank/absent."""
    values = qs.get(key)
    if not values:
        return None
    stripped = (values[0] or "").strip()
    return stripped or None


def _to_int(value: str) -> int | None:
    """Parse an int, returning None on failure (drops the param)."""
    try:
        return int(value)
    except ValueError:
        return None


def _to_hours_from_days(value: str) -> int | None:
    """Convert an Indeed ``fromage`` (days) value to hours (N * 24)."""
    days = _to_int(value)
    return None if days is None else days * 24


__all__ = ["parse_search_url"]
