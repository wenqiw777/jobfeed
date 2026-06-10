"""Unit tests for the status_query CLI commands (Phase 6).

Tests cover:
- _print_json output format
- _print_markdown output format
- _print_plain output format
- list_cmd and stats command registration
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from io import StringIO
from unittest.mock import patch

import pytest

from jobfeed.cli.status_query import (
    _print_json,
    _print_markdown,
    _print_plain,
    list_cmd,
    stats,
)
from jobfeed.domain.models import JobStatus
from jobfeed.domain.models_status import StatusInfo

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_FIXED_NOW = datetime(2026, 6, 10, 12, 0, tzinfo=UTC)
_FIXED_FU = datetime(2026, 6, 17, 12, 0, tzinfo=UTC)


def _make_status_info(
    job_id: str = "42",
    status: str = "applied",
    next_followup_at: datetime | None = None,
) -> StatusInfo:
    """Build a StatusInfo fixture."""
    return StatusInfo(
        job_id=job_id,
        status=JobStatus(status),
        next_followup_at=next_followup_at,
        last_status_change_at=_FIXED_NOW,
    )


# ---------------------------------------------------------------------------
# _print_json
# ---------------------------------------------------------------------------


class TestPrintJson:
    """Tests for status_query._print_json."""

    def test_empty_list_prints_empty_array(self) -> None:
        """_print_json with no rows should print '[]'."""
        lines: list[str] = []
        with patch("click.echo", side_effect=lines.append):
            _print_json([])
        assert lines == ["[]"]

    def test_single_row_valid_json(self) -> None:
        """_print_json with one row should produce valid JSON."""
        row = _make_status_info("10", "applied")
        lines: list[str] = []
        with patch("click.echo", side_effect=lines.append):
            _print_json([row])
        assert len(lines) == 1
        data = json.loads(lines[0])
        assert isinstance(data, list)
        assert len(data) == 1

    def test_json_row_has_id_status_followup(self) -> None:
        """Each JSON element should have 'id', 'status', 'next_followup_at'."""
        row = _make_status_info("42", "interviewing", _FIXED_FU)
        lines: list[str] = []
        with patch("click.echo", side_effect=lines.append):
            _print_json([row])
        data = json.loads(lines[0])
        item = data[0]
        assert item["id"] == "42"
        assert item["status"] == "interviewing"
        assert item["next_followup_at"] == _FIXED_FU.isoformat()

    def test_json_followup_none_when_absent(self) -> None:
        """When next_followup_at is None, JSON field should be null."""
        row = _make_status_info("5", "scored", None)
        lines: list[str] = []
        with patch("click.echo", side_effect=lines.append):
            _print_json([row])
        data = json.loads(lines[0])
        assert data[0]["next_followup_at"] is None

    def test_json_multiple_rows(self) -> None:
        """_print_json should handle multiple rows in a single JSON array."""
        rows = [
            _make_status_info("1", "applied"),
            _make_status_info("2", "interviewing"),
            _make_status_info("3", "rejected"),
        ]
        lines: list[str] = []
        with patch("click.echo", side_effect=lines.append):
            _print_json(rows)
        data = json.loads(lines[0])
        assert len(data) == 3
        ids = [item["id"] for item in data]
        assert ids == ["1", "2", "3"]


# ---------------------------------------------------------------------------
# _print_markdown
# ---------------------------------------------------------------------------


class TestPrintMarkdown:
    """Tests for status_query._print_markdown."""

    def test_header_is_printed(self) -> None:
        """_print_markdown should always print the table header."""
        lines: list[str] = []
        with patch("click.echo", side_effect=lines.append):
            _print_markdown([])
        assert "| id | status | next_followup_at |" in lines[0]
        assert "|----|--------|------------------|" in lines[1]

    def test_row_included_in_table(self) -> None:
        """Each row should appear as a markdown table row."""
        row = _make_status_info("99", "ghosted")
        lines: list[str] = []
        with patch("click.echo", side_effect=lines.append):
            _print_markdown([row])
        row_line = lines[2]
        assert "99" in row_line
        assert "ghosted" in row_line

    def test_followup_in_row(self) -> None:
        """next_followup_at should appear as ISO string in the table row."""
        row = _make_status_info("7", "applied", _FIXED_FU)
        lines: list[str] = []
        with patch("click.echo", side_effect=lines.append):
            _print_markdown([row])
        row_line = lines[2]
        assert _FIXED_FU.isoformat() in row_line

    def test_empty_followup_when_none(self) -> None:
        """When next_followup_at is None, the cell should be empty."""
        row = _make_status_info("8", "scored", None)
        lines: list[str] = []
        with patch("click.echo", side_effect=lines.append):
            _print_markdown([row])
        row_line = lines[2]
        # Should contain pipe separators but no followup timestamp
        assert "|  |" in row_line or "| 8 | scored |  |" in row_line

    def test_multiple_rows_each_on_own_line(self) -> None:
        """Each row should produce its own output line."""
        rows = [_make_status_info(str(i), "new") for i in range(3)]
        lines: list[str] = []
        with patch("click.echo", side_effect=lines.append):
            _print_markdown(rows)
        # 2 header lines + 3 data rows
        assert len(lines) == 5


# ---------------------------------------------------------------------------
# _print_plain
# ---------------------------------------------------------------------------


class TestPrintPlain:
    """Tests for status_query._print_plain."""

    def test_empty_list_produces_no_output(self) -> None:
        """_print_plain with no rows should produce no output."""
        lines: list[str] = []
        with patch("click.echo", side_effect=lines.append):
            _print_plain([])
        assert lines == []

    def test_row_contains_job_id_and_status(self) -> None:
        """Each row should contain job_id and status."""
        row = _make_status_info("55", "shortlisted")
        lines: list[str] = []
        with patch("click.echo", side_effect=lines.append):
            _print_plain([row])
        assert "55" in lines[0]
        assert "shortlisted" in lines[0]

    def test_followup_appended_when_present(self) -> None:
        """When next_followup_at is set, it should appear with 'followup=' prefix."""
        row = _make_status_info("12", "applied", _FIXED_FU)
        lines: list[str] = []
        with patch("click.echo", side_effect=lines.append):
            _print_plain([row])
        assert "followup=" in lines[0]
        assert _FIXED_FU.isoformat() in lines[0]

    def test_no_followup_tag_when_none(self) -> None:
        """When next_followup_at is None, no 'followup=' should appear."""
        row = _make_status_info("13", "scored", None)
        lines: list[str] = []
        with patch("click.echo", side_effect=lines.append):
            _print_plain([row])
        assert "followup=" not in lines[0]

    def test_multiple_rows_each_on_own_line(self) -> None:
        """Each row should produce a separate line."""
        rows = [_make_status_info(str(i), "new") for i in range(4)]
        lines: list[str] = []
        with patch("click.echo", side_effect=lines.append):
            _print_plain(rows)
        assert len(lines) == 4


# ---------------------------------------------------------------------------
# CLI registration
# ---------------------------------------------------------------------------


class TestStatusQueryRegistration:
    """Tests that list and stats commands are registered in the CLI."""

    def test_list_registered_in_cli(self) -> None:
        """The 'list' command should be registered in the CLI."""
        from jobfeed.cli import cli

        assert "list" in cli.commands

    def test_stats_registered_in_cli(self) -> None:
        """The 'stats' command should be registered in the CLI."""
        from jobfeed.cli import cli

        assert "stats" in cli.commands

    def test_list_help_exits_cleanly(self) -> None:
        """list --help should exit with code 0."""
        from click.testing import CliRunner

        runner = CliRunner()
        result = runner.invoke(list_cmd, ["--help"])
        assert result.exit_code == 0

    def test_stats_help_exits_cleanly(self) -> None:
        """stats --help should exit with code 0."""
        from click.testing import CliRunner

        runner = CliRunner()
        result = runner.invoke(stats, ["--help"])
        assert result.exit_code == 0

    def test_list_has_status_option(self) -> None:
        """list command should accept --status option."""
        param_names = {p.name for p in list_cmd.params}
        assert "status_filter" in param_names

    def test_list_has_needs_followup_flag(self) -> None:
        """list command should accept --needs-followup flag."""
        param_names = {p.name for p in list_cmd.params}
        assert "needs_followup" in param_names

    def test_list_has_no_response_option(self) -> None:
        """list command should accept --no-response option."""
        param_names = {p.name for p in list_cmd.params}
        assert "no_response_days" in param_names

    def test_stats_has_window_option(self) -> None:
        """stats command should accept --window option."""
        param_names = {p.name for p in stats.params}
        assert "window_days" in param_names

    def test_stats_window_default_is_30(self) -> None:
        """stats --window should default to 30."""
        window_param = next(p for p in stats.params if p.name == "window_days")
        assert window_param.default == 30

    def test_stats_has_by_resume_flag(self) -> None:
        """stats command should accept --by-resume flag."""
        param_names = {p.name for p in stats.params}
        assert "by_resume" in param_names