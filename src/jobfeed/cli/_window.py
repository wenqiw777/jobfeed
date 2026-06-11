"""Shared time-window parsing for CLI date options.

Accepted forms: ``Nd`` (N days), ``Nw`` (N weeks) relative to now, or a
bare ``YYYY-MM-DD`` meaning that date at midnight UTC. ``parse_window``
offsets forward (e.g. ``followup --in``); ``parse_window_back`` offsets
backward (e.g. ``list --days``). Absolute dates parse identically in both.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

import click

_RELATIVE_RE = re.compile(r"^(\d+)([dw])$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DAYS_PER_UNIT = {"d": 1, "w": 7}


def parse_window(value: str) -> datetime:
    """Parse a forward time window into an aware UTC datetime.

    Args:
        value: ``Nd``/``Nw`` for now plus N days/weeks, or ``YYYY-MM-DD``
            for that date at midnight UTC.

    Returns:
        A timezone-aware UTC datetime.

    Raises:
        click.BadParameter: If ``value`` is not an accepted form.
    """
    return _parse(value, direction=1)


def parse_window_back(value: str) -> datetime:
    """Parse a backward time window into an aware UTC datetime.

    Args:
        value: ``Nd``/``Nw`` for now minus N days/weeks, or ``YYYY-MM-DD``
            for that date at midnight UTC.

    Returns:
        A timezone-aware UTC datetime.

    Raises:
        click.BadParameter: If ``value`` is not an accepted form.
    """
    return _parse(value, direction=-1)


def _parse(value: str, *, direction: int) -> datetime:
    relative = _RELATIVE_RE.match(value)
    if relative:
        return _relative_offset(relative, direction, value)
    if _DATE_RE.match(value):
        return _absolute_date(value)
    raise _bad(value)


def _relative_offset(match: re.Match[str], direction: int, value: str) -> datetime:
    count = int(match.group(1))
    if count <= 0:
        raise _bad(value)
    days = count * _DAYS_PER_UNIT[match.group(2)]
    return datetime.now(UTC) + direction * timedelta(days=days)


def _absolute_date(value: str) -> datetime:
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d")
    except ValueError as exc:
        raise _bad(value) from exc
    return parsed.replace(tzinfo=UTC)


def _bad(value: str) -> click.BadParameter:
    return click.BadParameter(
        f"{value!r} is not a valid window; expected 'Nd' (days), "
        "'Nw' (weeks), or YYYY-MM-DD"
    )


__all__ = ["parse_window", "parse_window_back"]
