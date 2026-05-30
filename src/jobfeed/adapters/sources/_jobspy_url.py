"""Site-aware search-URL -> JobSpy kwargs parsing (pure stdlib).

Split out of ``_jobspy.py`` to keep that module under the 300-line gate. This
module touches NO jobspy/pandas types — it only turns a search URL's query
string into the keyword arguments ``jobspy.scrape_jobs`` accepts. Indeed and
LinkedIn use different query keys, so ``parse_search_url`` branches on
``site_name``.

Key maps::

    Indeed   (legacy indeed.py:109-135):  q -> search_term, l -> location,
             fromage=N -> hours_old=N*24, radius -> distance
    LinkedIn (Phase 4 plan, no legacy port): keywords -> search_term,
             location -> location, distance -> distance,
             f_TPR=r<seconds> -> hours_old = seconds // 3600
"""

from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs, urlparse


def parse_search_url(site_name: str, url: str) -> dict[str, Any]:
    """Translate a search URL into JobSpy kwargs, branching on site.

    Unrecognized params are dropped (JobSpy supplies its own defaults).

    Args:
        site_name: ``"indeed"`` or ``"linkedin"``.
        url: User-pasted search URL.

    Returns:
        JobSpy keyword arguments parsed from the URL's query string.
    """
    qs = parse_qs(urlparse(url).query)
    if site_name == "linkedin":
        return _parse_linkedin_qs(qs)
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


def _parse_linkedin_qs(qs: dict[str, list[str]]) -> dict[str, Any]:
    """Map LinkedIn query params: keywords/location/distance/f_TPR.

    LinkedIn search URLs use ``keywords``/``location``/``distance`` and encode
    freshness as ``f_TPR=r<seconds>`` (e.g. ``r86400`` = last 24h). There is no
    legacy JobSpy-LinkedIn parser to port, so these keys are mapped per the
    Phase 4 plan (Task 2): seconds -> hours via integer division.
    """
    out: dict[str, Any] = {}
    if term := _first(qs, "keywords"):
        out["search_term"] = term
    if location := _first(qs, "location"):
        out["location"] = location
    if (distance := _first(qs, "distance")) and (parsed := _to_int(distance)):
        out["distance"] = parsed
    if (tpr := _first(qs, "f_TPR")) and (hours := _tpr_to_hours(tpr)):
        out["hours_old"] = hours
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


def _tpr_to_hours(value: str) -> int | None:
    """Convert a LinkedIn ``f_TPR=r<seconds>`` value to whole hours.

    Accepts an optional leading ``r`` (LinkedIn's recency prefix). Sub-hour
    windows floor to 0, which JobSpy treats as "no window".
    """
    seconds = _to_int(value[1:] if value.startswith("r") else value)
    return None if seconds is None else seconds // 3600


__all__ = ["parse_search_url"]
