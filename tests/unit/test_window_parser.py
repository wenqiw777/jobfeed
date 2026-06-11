"""Unit tests for the shared CLI time-window parser."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import click
import pytest

from jobfeed.cli._window import parse_window, parse_window_back

ACCEPTED_FORMS_PATTERN = r"'Nd'.*'Nw'.*YYYY-MM-DD"


def test_parse_window_days_adds_forward_offset() -> None:
    """'Nd' should mean now plus N days."""
    before = datetime.now(UTC) + timedelta(days=7)
    result = parse_window("7d")
    after = datetime.now(UTC) + timedelta(days=7)
    assert before <= result <= after


def test_parse_window_weeks_adds_forward_offset() -> None:
    """'Nw' should mean now plus N weeks."""
    before = datetime.now(UTC) + timedelta(weeks=2)
    result = parse_window("2w")
    after = datetime.now(UTC) + timedelta(weeks=2)
    assert before <= result <= after


def test_parse_window_back_days_subtracts_offset() -> None:
    """'Nd' should mean now minus N days for the backward parser."""
    before = datetime.now(UTC) - timedelta(days=7)
    result = parse_window_back("7d")
    after = datetime.now(UTC) - timedelta(days=7)
    assert before <= result <= after


def test_parse_window_back_weeks_subtracts_offset() -> None:
    """'Nw' should mean now minus N weeks for the backward parser."""
    before = datetime.now(UTC) - timedelta(weeks=2)
    result = parse_window_back("2w")
    after = datetime.now(UTC) - timedelta(weeks=2)
    assert before <= result <= after


def test_parse_window_absolute_date_is_midnight_utc() -> None:
    """A bare YYYY-MM-DD should be that date at midnight UTC."""
    assert parse_window("2026-07-01") == datetime(2026, 7, 1, tzinfo=UTC)


def test_parse_window_back_absolute_date_does_not_flip() -> None:
    """Absolute dates parse identically in both directions."""
    assert parse_window_back("2026-07-01") == datetime(2026, 7, 1, tzinfo=UTC)


def test_parse_window_results_are_aware_utc() -> None:
    """Every accepted form should return a timezone-aware UTC datetime."""
    for value in ("7d", "2w", "2026-07-01"):
        for func in (parse_window, parse_window_back):
            result = func(value)
            assert result.tzinfo is not None
            assert result.utcoffset() == timedelta(0)


@pytest.mark.parametrize(
    "value",
    ["7", "0d", "-3d", "7x", "", "0w", "d", "2026-07-01x", "1.5d"],
)
def test_parse_window_rejects_invalid_forms(value: str) -> None:
    """Bare ints, zero/negative N, and junk strings raise BadParameter."""
    with pytest.raises(click.BadParameter, match=ACCEPTED_FORMS_PATTERN):
        parse_window(value)


@pytest.mark.parametrize("value", ["7", "0d", "-3d", "7x", ""])
def test_parse_window_back_rejects_invalid_forms(value: str) -> None:
    """The backward parser shares the same validation rules."""
    with pytest.raises(click.BadParameter, match=ACCEPTED_FORMS_PATTERN):
        parse_window_back(value)


def test_parse_window_rejects_invalid_calendar_date() -> None:
    """A YYYY-MM-DD shaped string with an impossible date is rejected."""
    with pytest.raises(click.BadParameter, match=ACCEPTED_FORMS_PATTERN):
        parse_window("2026-13-45")
