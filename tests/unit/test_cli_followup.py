"""Unit tests for the followup CLI command (Phase 7, Task 7).

Offline coverage: the store is an AsyncMock injected as the Click context
object. DB-backed followup flows land in the Task 8 e2e parity suite.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from click.testing import CliRunner

from jobfeed.cli import cli
from jobfeed.cli.status import followup
from jobfeed.config import Settings

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_store(set_followup: bool = True) -> AsyncMock:
    store = AsyncMock()
    store.set_followup.return_value = set_followup
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
# followup
# ---------------------------------------------------------------------------


class TestFollowup:
    """Tests for ``followup``."""

    def test_default_window_is_seven_days(self) -> None:
        """Without --in, follow-up lands ~7 days out (legacy default)."""
        store = _make_store()

        result = CliRunner().invoke(followup, ["12"], obj=_make_app(store))

        assert result.exit_code == 0, result.output
        store.set_followup.assert_awaited_once()
        kwargs = store.set_followup.await_args.kwargs
        assert kwargs["job_id"] == "12"
        at = kwargs["at"]
        assert at.tzinfo is not None
        delta = at - datetime.now(UTC)
        assert timedelta(days=6, hours=23) < delta < timedelta(days=7, minutes=5)
        assert "12" in result.output

    def test_explicit_window_in_weeks(self) -> None:
        """--in 2w schedules ~14 days out."""
        store = _make_store()

        result = CliRunner().invoke(
            followup, ["12", "--in", "2w"], obj=_make_app(store)
        )

        assert result.exit_code == 0, result.output
        at = store.set_followup.await_args.kwargs["at"]
        delta = at - datetime.now(UTC)
        assert timedelta(days=13, hours=23) < delta < timedelta(days=14, minutes=5)

    def test_absolute_date_passes_midnight_utc(self) -> None:
        """--in YYYY-MM-DD schedules that date at midnight UTC."""
        store = _make_store()

        result = CliRunner().invoke(
            followup, ["12", "--in", "2026-06-01"], obj=_make_app(store)
        )

        assert result.exit_code == 0, result.output
        at = store.set_followup.await_args.kwargs["at"]
        assert at == datetime(2026, 6, 1, tzinfo=UTC)

    def test_missing_job_exits_nonzero(self) -> None:
        """A store miss (False) reports not-found and exits nonzero."""
        store = _make_store(set_followup=False)

        result = CliRunner().invoke(followup, ["999"], obj=_make_app(store))

        assert result.exit_code != 0
        assert "999" in result.output
        assert "not found" in result.output

    def test_bad_window_is_usage_error(self) -> None:
        """An invalid window exits nonzero without touching the store."""
        store = _make_store()

        result = CliRunner().invoke(
            followup, ["12", "--in", "soon"], obj=_make_app(store)
        )

        assert result.exit_code != 0
        store.set_followup.assert_not_awaited()

    def test_registered_on_root_cli(self) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem():
            result = runner.invoke(cli, ["--help"])
        assert result.exit_code == 0, result.output
        assert "followup" in result.output
