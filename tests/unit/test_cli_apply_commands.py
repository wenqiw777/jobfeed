"""Unit tests for the apply CLI commands (Phase 6).

Tests cover:
- apply_cmd, apply_history, snapshots command registration
- _read_file helper raises ClickException on bad path
- Command option shapes
- Snapshot subgroup structure
"""

from __future__ import annotations

from pathlib import Path

import click
import pytest


class TestReadFileHelper:
    """Tests for the apply._read_file helper."""

    def test_read_file_returns_content(self, tmp_path: Path) -> None:
        """_read_file should return file content as a string."""
        from jobfeed.cli.apply import _read_file

        file = tmp_path / "resume.md"
        file.write_text("Alice Engineer\nPython, Go, SQL", encoding="utf-8")
        result = _read_file(file)
        assert result == "Alice Engineer\nPython, Go, SQL"

    def test_read_file_missing_raises_click_exception(self, tmp_path: Path) -> None:
        """_read_file with a non-existent path should raise click.ClickException."""
        from jobfeed.cli.apply import _read_file

        missing = tmp_path / "not_here.txt"
        with pytest.raises(click.ClickException, match="cannot read"):
            _read_file(missing)

    def test_read_file_exception_includes_path(self, tmp_path: Path) -> None:
        """The ClickException message should contain the bad file path."""
        from jobfeed.cli.apply import _read_file

        missing = tmp_path / "my_resume.md"
        with pytest.raises(click.ClickException) as exc_info:
            _read_file(missing)
        assert "my_resume.md" in str(exc_info.value.format_message())

    def test_read_file_multiline(self, tmp_path: Path) -> None:
        """_read_file should preserve newlines in the file."""
        from jobfeed.cli.apply import _read_file

        file = tmp_path / "multi.txt"
        content = "line 1\nline 2\nline 3\n"
        file.write_text(content, encoding="utf-8")
        assert _read_file(file) == content


class TestApplyCmdRegistration:
    """Tests that apply_cmd is registered in the CLI."""

    def test_apply_registered_in_cli(self) -> None:
        """The 'apply' command should be registered in the CLI."""
        from jobfeed.cli import cli

        assert "apply" in cli.commands

    def test_apply_help_exits_cleanly(self) -> None:
        """apply --help should exit with code 0."""
        from click.testing import CliRunner

        from jobfeed.cli.apply import apply_cmd

        runner = CliRunner()
        result = runner.invoke(apply_cmd, ["--help"])
        assert result.exit_code == 0

    def test_apply_has_tailored_option(self) -> None:
        """apply command should accept --tailored option."""
        from jobfeed.cli.apply import apply_cmd

        param_names = {p.name for p in apply_cmd.params}
        assert "tailored_path" in param_names

    def test_apply_has_cover_letter_option(self) -> None:
        """apply command should accept --cover-letter option."""
        from jobfeed.cli.apply import apply_cmd

        param_names = {p.name for p in apply_cmd.params}
        assert "cover_letter_path" in param_names

    def test_apply_has_variant_option(self) -> None:
        """apply command should accept --variant option."""
        from jobfeed.cli.apply import apply_cmd

        param_names = {p.name for p in apply_cmd.params}
        assert "variant" in param_names


class TestApplyHistoryRegistration:
    """Tests that apply-history command is registered."""

    def test_apply_history_registered_in_cli(self) -> None:
        """The 'apply-history' command should be registered in the CLI."""
        from jobfeed.cli import cli

        assert "apply-history" in cli.commands

    def test_apply_history_help_exits_cleanly(self) -> None:
        """apply-history --help should exit with code 0."""
        from click.testing import CliRunner

        from jobfeed.cli.apply import apply_history

        runner = CliRunner()
        result = runner.invoke(apply_history, ["--help"])
        assert result.exit_code == 0

    def test_apply_history_has_limit_option(self) -> None:
        """apply-history command should accept --limit option."""
        from jobfeed.cli.apply import apply_history

        param_names = {p.name for p in apply_history.params}
        assert "limit" in param_names

    def test_apply_history_limit_default_is_50(self) -> None:
        """apply-history --limit should default to 50."""
        from jobfeed.cli.apply import apply_history

        limit_param = next(p for p in apply_history.params if p.name == "limit")
        assert limit_param.default == 50


class TestSnapshotsGroup:
    """Tests for the snapshots command group."""

    def test_snapshots_registered_in_cli(self) -> None:
        """The 'snapshots' command group should be registered in the CLI."""
        from jobfeed.cli import cli

        assert "snapshots" in cli.commands

    def test_snapshots_help_exits_cleanly(self) -> None:
        """snapshots --help should exit with code 0."""
        from click.testing import CliRunner

        from jobfeed.cli.apply import snapshots

        runner = CliRunner()
        result = runner.invoke(snapshots, ["--help"])
        assert result.exit_code == 0

    def test_snapshots_is_group(self) -> None:
        """snapshots should be a Click Group."""
        from jobfeed.cli.apply import snapshots

        assert isinstance(snapshots, click.Group)

    def test_snapshots_has_show_subcommand(self) -> None:
        """snapshots group should have a 'show' subcommand."""
        from jobfeed.cli.apply import snapshots

        assert "show" in snapshots.commands

    def test_snapshots_has_list_subcommand(self) -> None:
        """snapshots group should have a 'list' subcommand."""
        from jobfeed.cli.apply import snapshots

        assert "list" in snapshots.commands

    def test_snapshots_has_diff_subcommand(self) -> None:
        """snapshots group should have a 'diff' subcommand."""
        from jobfeed.cli.apply import snapshots

        assert "diff" in snapshots.commands

    def test_snapshots_show_help_exits_cleanly(self) -> None:
        """snapshots show --help should exit with code 0."""
        from click.testing import CliRunner

        from jobfeed.cli.apply import snapshots

        runner = CliRunner()
        result = runner.invoke(snapshots, ["show", "--help"])
        assert result.exit_code == 0

    def test_snapshots_diff_help_exits_cleanly(self) -> None:
        """snapshots diff --help should exit with code 0."""
        from click.testing import CliRunner

        from jobfeed.cli.apply import snapshots

        runner = CliRunner()
        result = runner.invoke(snapshots, ["diff", "--help"])
        assert result.exit_code == 0


class TestApplyModuleExports:
    """Tests for the apply module __all__ contract."""

    def test_all_exports_correct(self) -> None:
        """apply.py should export apply_cmd, apply_history, snapshots."""
        from jobfeed.cli.apply import __all__ as apply_all

        assert set(apply_all) == {"apply_cmd", "apply_history", "snapshots"}