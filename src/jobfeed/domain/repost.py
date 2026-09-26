"""Repost ordering uses the first discovery day in the user's timezone."""

from datetime import datetime
from zoneinfo import ZoneInfo

TRIAGE_TIMEZONE = ZoneInfo("America/Detroit")


def discovery_day(value: datetime | str) -> int:
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    return value.astimezone(TRIAGE_TIMEZONE).date().toordinal()
