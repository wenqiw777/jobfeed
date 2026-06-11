"""Unit tests for the enrich-paste CLI command (Phase 7, Task 7).

Offline coverage: the store is an AsyncMock injected as the Click context
object. DB-backed paste flows land in the Task 8 e2e parity suite.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from click.testing import CliRunner

from jobfeed.cli import cli
from jobfeed.cli.enrich import enrich_paste
from jobfeed.config import Settings

_JD_TEXT = "Senior Backend Engineer. " * 50  # > 1000 chars -> FULL band

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_store(**overrides: Any) -> AsyncMock:
    store = AsyncMock()
    store.enrich_paste.return_value = overrides.get("enrich_paste", "77")
    return store


def _make_app(store: AsyncMock) -> dict[str, Any]:
    return {
        "settings": Settings.model_validate({}),
        "store": store,
        "sources": {},
        "scan_service": MagicMock(),
        "digest_service": MagicMock(),
        "logger": MagicMock(),
        "verbose": False,
    }


# ---------------------------------------------------------------------------
# enrich-paste
# ---------------------------------------------------------------------------


class TestEnrichPaste:
    """Tests for ``enrich-paste``."""

    def test_stdin_paste_defaults_to_linkedin(self) -> None:
        """JD text from stdin is stored under the default linkedin platform."""
        store = _make_store()

        result = CliRunner().invoke(
            enrich_paste, ["abc123"], input=_JD_TEXT, obj=_make_app(store)
        )

        assert result.exit_code == 0, result.output
        store.enrich_paste.assert_awaited_once_with(
            platform="linkedin",
            canonical_id="abc123",
            jd_text=_JD_TEXT,
        )
        assert "77" in result.output
        assert "full" in result.output

    def test_from_file_reads_path_not_stdin(self, tmp_path: Path) -> None:
        """--from-file reads the JD from the file."""
        jd_file = tmp_path / "jd.txt"
        jd_file.write_text(_JD_TEXT, encoding="utf-8")
        store = _make_store()

        result = CliRunner().invoke(
            enrich_paste,
            ["abc123", "--from-file", str(jd_file)],
            obj=_make_app(store),
        )

        assert result.exit_code == 0, result.output
        kwargs = store.enrich_paste.await_args.kwargs
        assert kwargs["jd_text"] == _JD_TEXT

    def test_platform_indeed_passes_through(self) -> None:
        """--platform indeed is forwarded to the store."""
        store = _make_store()

        result = CliRunner().invoke(
            enrich_paste,
            ["xyz", "--platform", "indeed"],
            input=_JD_TEXT,
            obj=_make_app(store),
        )

        assert result.exit_code == 0, result.output
        assert store.enrich_paste.await_args.kwargs["platform"] == "indeed"

    def test_unsupported_platform_rejected(self) -> None:
        """--platform only accepts linkedin and indeed."""
        store = _make_store()

        result = CliRunner().invoke(
            enrich_paste,
            ["xyz", "--platform", "monster"],
            input=_JD_TEXT,
            obj=_make_app(store),
        )

        assert result.exit_code != 0
        store.enrich_paste.assert_not_awaited()

    def test_empty_stdin_is_usage_error(self) -> None:
        """Whitespace-only JD text exits nonzero without touching the store."""
        store = _make_store()

        result = CliRunner().invoke(
            enrich_paste, ["abc123"], input="  \n\t ", obj=_make_app(store)
        )

        assert result.exit_code != 0
        assert "empty" in result.output.lower()
        store.enrich_paste.assert_not_awaited()

    def test_unknown_canonical_id_exits_nonzero(self) -> None:
        """The store's ValueError surfaces as a clean nonzero exit."""
        store = _make_store()
        store.enrich_paste.side_effect = ValueError("job not found: linkedin/missing")

        result = CliRunner().invoke(
            enrich_paste, ["missing"], input=_JD_TEXT, obj=_make_app(store)
        )

        assert result.exit_code != 0
        assert "job not found" in result.output
        assert "Traceback" not in result.output

    def test_registered_on_root_cli(self) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem():
            result = runner.invoke(cli, ["--help"])
        assert result.exit_code == 0, result.output
        assert "enrich-paste" in result.output
