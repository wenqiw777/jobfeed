"""Repost ordering uses the first discovery day in the user's timezone."""

from datetime import datetime
from zoneinfo import ZoneInfo

TRIAGE_TIMEZONE = ZoneInfo("America/Detroit")


def discovery_day(value: datetime | str) -> int:
    """Convert a timestamp to the user-local discovery day.

    Args:
        value: Source value to normalize.

    Returns:
        Ordinal day in America/Detroit.
    """
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    return value.astimezone(TRIAGE_TIMEZONE).date().toordinal()
