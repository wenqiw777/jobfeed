"""Unit tests for the interview CLI commands.

Offline coverage of ``cli/interview.py``: the pure ``_parse_datetime``
helper (accepted formats, rejection diagnostics) and registration of the
``interview`` group with its subcommands on the root CLI.
"""

from __future__ import annotations

from datetime import UTC, datetime

import click
import pytest

from jobfeed.cli import cli
from jobfeed.cli.interview import _parse_datetime, interview


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026-07-15", datetime(2026, 7, 15, tzinfo=UTC)),  # date only -> midnight
        ("2026-07-15T09:30", datetime(2026, 7, 15, 9, 30, tzinfo=UTC)),
        ("2026-07-15T09:30:45", datetime(2026, 7, 15, 9, 30, 45, tzinfo=UTC)),
        ("2026-07-15 14:00", datetime(2026, 7, 15, 14, 0, tzinfo=UTC)),
        ("2026-12-31T23:59", datetime(2026, 12, 31, 23, 59, tzinfo=UTC)),
        ("2000-01-01", datetime(2000, 1, 1, tzinfo=UTC)),  # year boundary
    ],
)
def test_parse_datetime_accepts_supported_forms(value: str, expected: datetime) -> None:
    """Every supported form parses exactly, and naive inputs receive UTC."""
    result = _parse_datetime(value)
    assert result == expected
    assert result.tzinfo is UTC


@pytest.mark.parametrize(
    "value",
    ["not-a-date", "2026-07", "", "07/15/2026", "2026-13-01"],
)
def test_parse_datetime_rejects_invalid_forms(value: str) -> None:
    """Garbage, partial, US-style, and impossible dates raise BadParameter."""
    with pytest.raises(click.BadParameter) as exc_info:
        _parse_datetime(value)
    message = str(exc_info.value)
    assert "cannot parse datetime" in message
    assert value in message  # diagnostics echo the offending input


def test_interview_group_registration() -> None:
    """The interview group is on the root CLI with add/list/done wired."""
    assert "interview" in cli.commands
    assert isinstance(interview, click.Group)
    assert set(interview.commands) >= {"add", "list", "done"}

    add_params = {p.name for p in interview.commands["add"].params}
    assert "scheduled_at_str" in add_params  # --scheduled-at
    done_params = {p.name for p in interview.commands["done"].params}
    assert {"round_index", "notes"} <= done_params  # --round / --notes
