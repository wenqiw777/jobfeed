"""Unit tests for the reworked list CLI command (Phase 7, Task 7).

Offline coverage: the store is an AsyncMock injected as the Click context
object. DB-backed list flows land in the Task 8 e2e parity suite.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from click.testing import CliRunner

from jobfeed.cli.status_query import list_cmd
from jobfeed.config import Settings
from jobfeed.domain.models_status import StatusFilter, StatusInfo
from jobfeed.domain.status import LIST_DEFAULT_STATUSES

_LAST_CHANGE = datetime(2026, 6, 8, 12, 0, tzinfo=UTC)
_FOLLOWUP = datetime(2026, 6, 15, tzinfo=UTC)
_SEVEN_DAYS = 7
_TWO_WEEKS_AS_DAYS = 14
_FIVE_ROWS = 5

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _row(**overrides: Any) -> StatusInfo:
    defaults: dict[str, Any] = {
        "job_id": "12",
        "status": "applied",
        "next_followup_at": _FOLLOWUP,
        "last_status_change_at": _LAST_CHANGE,
        "company": "Acme Robotics Corporation Worldwide",
        "title": "Senior Staff Software Engineer, Distributed Systems Platform",
    }
    defaults.update(overrides)
    return StatusInfo(**defaults)


def _make_store(rows: list[StatusInfo] | None = None) -> AsyncMock:
    store = AsyncMock()
    store.list_statuses.return_value = rows if rows is not None else [_row()]
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


def _sent_filter(store: AsyncMock) -> StatusFilter:
    return store.list_statuses.await_args.args[0]


# ---------------------------------------------------------------------------
# Filter wiring
# ---------------------------------------------------------------------------


class TestListFilters:
    """Flag-to-StatusFilter wiring."""

    def test_default_statuses_hide_new_and_archived(self) -> None:
        """Without --status, LIST_DEFAULT_STATUSES is applied."""
        store = _make_store()

        result = CliRunner().invoke(list_cmd, [], obj=_make_app(store))

        assert result.exit_code == 0, result.output
        assert _sent_filter(store).statuses == LIST_DEFAULT_STATUSES

    def test_status_all_is_no_filter_escape_hatch(self) -> None:
        """--status all sends no status filter at all."""
        store = _make_store()

        result = CliRunner().invoke(list_cmd, ["--status", "all"], obj=_make_app(store))

        assert result.exit_code == 0, result.output
        assert _sent_filter(store).statuses is None

    def test_explicit_status_opts_in(self) -> None:
        """--status new opts back into a default-hidden status."""
        store = _make_store()

        result = CliRunner().invoke(list_cmd, ["--status", "new"], obj=_make_app(store))

        assert result.exit_code == 0, result.output
        assert _sent_filter(store).statuses == frozenset({"new"})

    def test_comma_separated_statuses(self) -> None:
        """--status splits on commas and strips whitespace."""
        store = _make_store()

        result = CliRunner().invoke(
            list_cmd, ["--status", "applied, interviewing"], obj=_make_app(store)
        )

        assert result.exit_code == 0, result.output
        assert _sent_filter(store).statuses == frozenset({"applied", "interviewing"})

    def test_days_relative_window_becomes_since_cutoff(self) -> None:
        """--days 7d becomes since=now-7d on the filter (days stays unset)."""
        store = _make_store()

        before = datetime.now(UTC)
        result = CliRunner().invoke(list_cmd, ["--days", "7d"], obj=_make_app(store))
        after = datetime.now(UTC)

        assert result.exit_code == 0, result.output
        sent = _sent_filter(store)
        assert sent.days is None
        offset = timedelta(days=_SEVEN_DAYS)
        assert before - offset <= sent.since <= after - offset

    def test_days_weeks_form(self) -> None:
        """--days 2w becomes since=now-14d."""
        store = _make_store()

        before = datetime.now(UTC)
        result = CliRunner().invoke(list_cmd, ["--days", "2w"], obj=_make_app(store))
        after = datetime.now(UTC)

        assert result.exit_code == 0, result.output
        offset = timedelta(days=_TWO_WEEKS_AS_DAYS)
        assert before - offset <= _sent_filter(store).since <= after - offset

    def test_days_absolute_date_is_exact_midnight_utc(self) -> None:
        """--days YYYY-MM-DD becomes since=that date's midnight UTC, exactly."""
        store = _make_store()

        result = CliRunner().invoke(
            list_cmd, ["--days", "2026-06-01"], obj=_make_app(store)
        )

        assert result.exit_code == 0, result.output
        sent = _sent_filter(store)
        assert sent.since == datetime(2026, 6, 1, tzinfo=UTC)
        assert sent.days is None

    def test_days_bad_window_is_usage_error(self) -> None:
        """An invalid --days value exits nonzero without querying."""
        store = _make_store()

        result = CliRunner().invoke(
            list_cmd, ["--days", "lately"], obj=_make_app(store)
        )

        assert result.exit_code != 0
        store.list_statuses.assert_not_awaited()

    def test_notes_contain_limit_and_days_compose(self) -> None:
        """--days, --notes-contain, and --limit all land on one filter."""
        store = _make_store()

        before = datetime.now(UTC)
        result = CliRunner().invoke(
            list_cmd,
            ["--days", "7d", "--notes-contain", "recruiter", "--limit", "5"],
            obj=_make_app(store),
        )
        after = datetime.now(UTC)

        assert result.exit_code == 0, result.output
        sent = _sent_filter(store)
        offset = timedelta(days=_SEVEN_DAYS)
        assert before - offset <= sent.since <= after - offset
        assert sent.notes_contain == "recruiter"
        assert sent.limit == _FIVE_ROWS

    def test_limit_must_be_positive(self) -> None:
        """--limit 0 is rejected by IntRange."""
        store = _make_store()

        result = CliRunner().invoke(list_cmd, ["--limit", "0"], obj=_make_app(store))

        assert result.exit_code != 0
        store.list_statuses.assert_not_awaited()


# ---------------------------------------------------------------------------
# Empty results
# ---------------------------------------------------------------------------


class TestListEmpty:
    """Empty-result exit codes."""

    def test_empty_exits_one(self) -> None:
        store = _make_store(rows=[])

        result = CliRunner().invoke(list_cmd, [], obj=_make_app(store))

        assert result.exit_code == 1
        assert "No matching jobs" in result.output

    def test_allow_empty_exits_zero(self) -> None:
        store = _make_store(rows=[])

        result = CliRunner().invoke(list_cmd, ["--allow-empty"], obj=_make_app(store))

        assert result.exit_code == 0, result.output
        assert "No matching jobs" in result.output

    def test_allow_empty_json_emits_empty_array(self) -> None:
        """Empty result with --allow-empty --json exits 0 and prints []."""
        store = _make_store(rows=[])

        result = CliRunner().invoke(
            list_cmd, ["--allow-empty", "--json"], obj=_make_app(store)
        )

        assert result.exit_code == 0, result.output
        assert result.stdout.strip() == "[]"


# ---------------------------------------------------------------------------
# Output formats
# ---------------------------------------------------------------------------


class TestListOutput:
    """Plain / markdown / JSON rendering."""

    def test_plain_truncates_company_and_title(self) -> None:
        """Plain view clips company to 24 and title to 40 chars."""
        store = _make_store()

        result = CliRunner().invoke(list_cmd, [], obj=_make_app(store))

        assert result.exit_code == 0, result.output
        assert "Acme Robotics Corporatio" in result.output  # 24 chars
        assert "Acme Robotics Corporation Worldwide" not in result.output
        assert "Senior Staff Software Engineer, Distributed" not in result.output
        assert "12" in result.output
        assert "applied" in result.output
        assert "2026-06-08" in result.output
        assert "2026-06-15" in result.output

    def test_markdown_has_all_columns(self) -> None:
        store = _make_store()

        result = CliRunner().invoke(list_cmd, ["--md"], obj=_make_app(store))

        assert result.exit_code == 0, result.output
        header = result.output.splitlines()[0]
        for col in ("id", "status", "company", "title", "last_change", "followup"):
            assert col in header
        assert "Acme Robotics Corporation Worldwide" not in result.output

    def test_json_carries_full_company_and_title(self) -> None:
        """JSON rows keep untruncated company/title values."""
        store = _make_store()

        result = CliRunner().invoke(list_cmd, ["--json"], obj=_make_app(store))

        assert result.exit_code == 0, result.output
        rows = json.loads(result.output)
        assert rows[0]["id"] == "12"
        assert rows[0]["status"] == "applied"
        assert rows[0]["company"] == "Acme Robotics Corporation Worldwide"
        assert (
            rows[0]["title"]
            == "Senior Staff Software Engineer, Distributed Systems Platform"
        )
        assert rows[0]["last_status_change_at"] == _LAST_CHANGE.isoformat()
        assert rows[0]["next_followup_at"] == _FOLLOWUP.isoformat()

    def test_plain_handles_missing_optional_fields(self) -> None:
        """Rows without company/title/followup still render."""
        store = _make_store(
            rows=[_row(company=None, title=None, next_followup_at=None)]
        )

        result = CliRunner().invoke(list_cmd, [], obj=_make_app(store))

        assert result.exit_code == 0, result.output
        assert "12" in result.output
