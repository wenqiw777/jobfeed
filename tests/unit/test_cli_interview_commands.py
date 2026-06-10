"""Unit tests for the interview CLI commands (Phase 6).

Tests cover:
- _parse_datetime helper function (pure, no DB required)
- interview command group structure
- CLI command registration in the main cli group
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from jobfeed.cli.interview import _parse_datetime, interview


class TestParseDatetime:
    """Tests for interview._parse_datetime helper."""

    def test_date_only_format(self) -> None:
        """YYYY-MM-DD should be parsed as UTC midnight."""
        result = _parse_datetime("2026-07-15")
        assert result == datetime(2026, 7, 15, 0, 0, tzinfo=UTC)

    def test_datetime_with_minutes_format(self) -> None:
        """YYYY-MM-DDTHH:MM should be parsed as UTC datetime."""
        result = _parse_datetime("2026-07-15T09:30")
        assert result == datetime(2026, 7, 15, 9, 30, tzinfo=UTC)

    def test_datetime_with_seconds_format(self) -> None:
        """YYYY-MM-DDTHH:MM:SS should be parsed as UTC datetime."""
        result = _parse_datetime("2026-07-15T09:30:45")
        assert result == datetime(2026, 7, 15, 9, 30, 45, tzinfo=UTC)

    def test_datetime_space_separator(self) -> None:
        """YYYY-MM-DD HH:MM (space separator) should be parsed as UTC datetime."""
        result = _parse_datetime("2026-07-15 14:00")
        assert result == datetime(2026, 7, 15, 14, 0, tzinfo=UTC)

    def test_naive_datetime_gets_utc_timezone(self) -> None:
        """Parsed datetimes without timezone info should receive UTC."""
        result = _parse_datetime("2026-01-01")
        assert result.tzinfo is UTC

    def test_invalid_format_raises_bad_parameter(self) -> None:
        """Unrecognizable datetime strings should raise click.BadParameter."""
        import click

        with pytest.raises(click.BadParameter, match="cannot parse datetime"):
            _parse_datetime("not-a-date")

    def test_partial_date_raises_bad_parameter(self) -> None:
        """Partial date like '2026-07' should raise click.BadParameter."""
        import click

        with pytest.raises(click.BadParameter, match="cannot parse datetime"):
            _parse_datetime("2026-07")

    def test_empty_string_raises_bad_parameter(self) -> None:
        """Empty string should raise click.BadParameter."""
        import click

        with pytest.raises(click.BadParameter, match="cannot parse datetime"):
            _parse_datetime("")

    def test_us_date_format_raises_bad_parameter(self) -> None:
        """US-style date 'MM/DD/YYYY' is not supported."""
        import click

        with pytest.raises(click.BadParameter, match="cannot parse datetime"):
            _parse_datetime("07/15/2026")

    def test_invalid_month_raises_bad_parameter(self) -> None:
        """Invalid month value should raise click.BadParameter."""
        import click

        with pytest.raises(click.BadParameter):
            _parse_datetime("2026-13-01")

    def test_error_message_includes_value(self) -> None:
        """Error message should include the bad input value."""
        import click

        bad_value = "garbage-input"
        with pytest.raises(click.BadParameter, match=bad_value):
            _parse_datetime(bad_value)

    def test_year_boundary_low(self) -> None:
        """Year 2000 dates should parse correctly."""
        result = _parse_datetime("2000-01-01")
        assert result.year == 2000

    def test_datetime_end_of_day(self) -> None:
        """23:59 should be a valid time."""
        result = _parse_datetime("2026-12-31T23:59")
        assert result.hour == 23
        assert result.minute == 59


class TestInterviewCommandStructure:
    """Tests for the interview command group structure."""

    def test_interview_is_a_group(self) -> None:
        """interview should be a Click Group."""
        import click

        assert isinstance(interview, click.Group)

    def test_interview_has_add_subcommand(self) -> None:
        """interview group should have an 'add' subcommand."""
        assert "add" in interview.commands

    def test_interview_has_list_subcommand(self) -> None:
        """interview group should have a 'list' subcommand."""
        assert "list" in interview.commands

    def test_interview_has_done_subcommand(self) -> None:
        """interview group should have a 'done' subcommand."""
        assert "done" in interview.commands

    def test_interview_add_has_scheduled_at_option(self) -> None:
        """interview add should accept --scheduled-at option."""
        add_cmd = interview.commands["add"]
        param_names = {p.name for p in add_cmd.params}
        assert "scheduled_at_str" in param_names

    def test_interview_done_has_round_option(self) -> None:
        """interview done should accept --round option."""
        done_cmd = interview.commands["done"]
        param_names = {p.name for p in done_cmd.params}
        assert "round_index" in param_names

    def test_interview_done_has_notes_option(self) -> None:
        """interview done should accept --notes option."""
        done_cmd = interview.commands["done"]
        param_names = {p.name for p in done_cmd.params}
        assert "notes" in param_names


class TestInterviewCliRegistration:
    """Tests that interview commands are registered in the CLI."""

    def test_interview_registered_in_cli(self) -> None:
        """The 'interview' command group should be registered in the CLI."""
        from jobfeed.cli import cli

        assert "interview" in cli.commands

    def test_interview_help_exits_cleanly(self) -> None:
        """interview --help should exit with code 0."""
        from click.testing import CliRunner

        from jobfeed.cli.interview import interview

        runner = CliRunner()
        result = runner.invoke(interview, ["--help"])
        assert result.exit_code == 0

    def test_interview_add_help_exits_cleanly(self) -> None:
        """interview add --help should exit with code 0."""
        from click.testing import CliRunner

        runner = CliRunner()
        result = runner.invoke(interview, ["add", "--help"])
        assert result.exit_code == 0

    def test_interview_list_help_exits_cleanly(self) -> None:
        """interview list --help should exit with code 0."""
        from click.testing import CliRunner

        runner = CliRunner()
        result = runner.invoke(interview, ["list", "--help"])
        assert result.exit_code == 0

    def test_interview_done_help_exits_cleanly(self) -> None:
        """interview done --help should exit with code 0."""
        from click.testing import CliRunner

        runner = CliRunner()
        result = runner.invoke(interview, ["done", "--help"])
        assert result.exit_code == 0