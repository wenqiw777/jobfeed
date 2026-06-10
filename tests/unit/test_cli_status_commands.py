"""Unit tests for the status mutation CLI commands (Phase 6).

Tests cover:
- mark, archive, note command registration in the CLI
- Command option shapes
- --help exits cleanly
"""

from __future__ import annotations

import pytest


class TestMarkCommand:
    """Tests for the mark CLI command."""

    def test_mark_registered_in_cli(self) -> None:
        """The 'mark' command should be registered in the CLI."""
        from jobfeed.cli import cli

        assert "mark" in cli.commands

    def test_mark_help_exits_cleanly(self) -> None:
        """mark --help should exit with code 0."""
        from click.testing import CliRunner

        from jobfeed.cli.status import mark

        runner = CliRunner()
        result = runner.invoke(mark, ["--help"])
        assert result.exit_code == 0

    def test_mark_has_status_option(self) -> None:
        """mark command should accept --status option."""
        from jobfeed.cli.status import mark

        param_names = {p.name for p in mark.params}
        assert "status" in param_names

    def test_mark_has_bulk_flag(self) -> None:
        """mark command should accept --bulk flag."""
        from jobfeed.cli.status import mark

        param_names = {p.name for p in mark.params}
        assert "bulk" in param_names

    def test_mark_has_force_flag(self) -> None:
        """mark command should accept --force flag."""
        from jobfeed.cli.status import mark

        param_names = {p.name for p in mark.params}
        assert "force" in param_names

    def test_mark_has_restore_flag(self) -> None:
        """mark command should accept --restore flag."""
        from jobfeed.cli.status import mark

        param_names = {p.name for p in mark.params}
        assert "restore" in param_names

    def test_mark_has_i_mean_it_flag(self) -> None:
        """mark command should accept --i-mean-it flag."""
        from jobfeed.cli.status import mark

        param_names = {p.name for p in mark.params}
        assert "i_mean_it" in param_names

    def test_mark_has_note_option(self) -> None:
        """mark command should accept --note option."""
        from jobfeed.cli.status import mark

        param_names = {p.name for p in mark.params}
        assert "note_text" in param_names

    def test_mark_has_resume_option(self) -> None:
        """mark command should accept --resume variant option."""
        from jobfeed.cli.status import mark

        param_names = {p.name for p in mark.params}
        assert "resume_variant" in param_names

    def test_mark_status_choice_includes_phase6_statuses(self) -> None:
        """mark --status should include the 11 Phase 6 status values."""
        from jobfeed.cli.status import mark

        status_param = next(p for p in mark.params if p.name == "status")
        choices = set(status_param.type.choices)
        expected_statuses = {
            "new",
            "scored",
            "shortlisted",
            "awaiting_referral",
            "applied",
            "interviewing",
            "offer",
            "rejected",
            "ghosted",
            "archived",
            "ignored",
        }
        assert expected_statuses == choices

    def test_mark_status_does_not_include_retired_statuses(self) -> None:
        """mark --status should NOT include retired sub-statuses."""
        from jobfeed.cli.status import mark

        status_param = next(p for p in mark.params if p.name == "status")
        choices = set(status_param.type.choices)
        retired = {"oa", "hr_call", "second_round", "final_round"}
        assert not retired.intersection(choices)


class TestArchiveCommand:
    """Tests for the archive CLI command."""

    def test_archive_registered_in_cli(self) -> None:
        """The 'archive' command should be registered in the CLI."""
        from jobfeed.cli import cli

        assert "archive" in cli.commands

    def test_archive_help_exits_cleanly(self) -> None:
        """archive --help should exit with code 0."""
        from click.testing import CliRunner

        from jobfeed.cli.status import archive

        runner = CliRunner()
        result = runner.invoke(archive, ["--help"])
        assert result.exit_code == 0

    def test_archive_has_force_flag(self) -> None:
        """archive command should accept --force flag."""
        from jobfeed.cli.status import archive

        param_names = {p.name for p in archive.params}
        assert "force" in param_names


class TestNoteCommand:
    """Tests for the note CLI command."""

    def test_note_registered_in_cli(self) -> None:
        """The 'note' command should be registered in the CLI."""
        from jobfeed.cli import cli

        assert "note" in cli.commands

    def test_note_help_exits_cleanly(self) -> None:
        """note --help should exit with code 0."""
        from click.testing import CliRunner

        from jobfeed.cli.status import note

        runner = CliRunner()
        result = runner.invoke(note, ["--help"])
        assert result.exit_code == 0


class TestStatusModuleExports:
    """Tests for the status module __all__ contract."""

    def test_all_exports_correct(self) -> None:
        """status.py should export mark, archive, note."""
        from jobfeed.cli.status import __all__ as status_all

        assert set(status_all) == {"archive", "mark", "note"}