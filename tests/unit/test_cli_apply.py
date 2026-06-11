"""Unit tests for apply --method/--notes and apply-history --resume (Task 7).

Offline coverage: the store is an AsyncMock injected as the Click context
object. DB-backed apply flows land in the Task 8 e2e parity suite.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from click.testing import CliRunner

from jobfeed.cli.apply import apply_cmd, apply_history
from jobfeed.config import Settings
from jobfeed.domain.models import ApplicationRecord

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_store(**overrides: Any) -> AsyncMock:
    store = AsyncMock()
    store.get_evaluation.return_value = None
    store.record_application_with_snapshots.return_value = overrides.get(
        "record_application_with_snapshots", True
    )
    store.compute_reapply_notice.return_value = overrides.get("compute_reapply_notice")
    store.list_applications.return_value = overrides.get("list_applications", [])
    return store


def _make_app(store: AsyncMock, master_resume: Path) -> dict[str, Any]:
    settings = Settings.model_validate(
        {"llm": {"master_resume_path": str(master_resume)}}
    )
    return {
        "settings": settings,
        "store": store,
        "sources": {},
        "scan_service": MagicMock(),
        "digest_service": MagicMock(),
        "logger": MagicMock(),
        "verbose": False,
    }


@pytest.fixture
def master_resume(tmp_path: Path) -> Path:
    path = tmp_path / "resume.md"
    path.write_text("Alice Engineer\nPython, Go, SQL\n", encoding="utf-8")
    return path


def _record(**overrides: Any) -> ApplicationRecord:
    defaults: dict[str, Any] = {
        "job_id": "42",
        "applied_at": datetime(2026, 6, 9, 10, 30, tzinfo=UTC),
    }
    defaults.update(overrides)
    return ApplicationRecord(**defaults)


# ---------------------------------------------------------------------------
# apply
# ---------------------------------------------------------------------------


class TestApplyFlags:
    """Tests for ``apply --method`` and ``apply --notes``."""

    def test_method_and_notes_land_on_record(self, master_resume: Path) -> None:
        store = _make_store()

        result = CliRunner().invoke(
            apply_cmd,
            ["42", "--method", "referral", "--notes", "via Sam"],
            obj=_make_app(store, master_resume),
        )

        assert result.exit_code == 0, result.output
        record = store.record_application_with_snapshots.call_args.args[0]
        assert record.application_method == "referral"
        assert record.notes == "via Sam"

    def test_method_defaults_to_web(self, master_resume: Path) -> None:
        store = _make_store()

        result = CliRunner().invoke(
            apply_cmd, ["42"], obj=_make_app(store, master_resume)
        )

        assert result.exit_code == 0, result.output
        record = store.record_application_with_snapshots.call_args.args[0]
        assert record.application_method == "web"
        assert record.notes is None

    def test_unsupported_method_rejected(self, master_resume: Path) -> None:
        store = _make_store()

        result = CliRunner().invoke(
            apply_cmd,
            ["42", "--method", "carrier-pigeon"],
            obj=_make_app(store, master_resume),
        )

        assert result.exit_code != 0
        store.record_application_with_snapshots.assert_not_awaited()

    def test_reapply_notice_printed_after_success(self, master_resume: Path) -> None:
        notice = "Active application at same company: 'SRE' (job 7, status=applied)"
        store = _make_store(compute_reapply_notice=notice)

        result = CliRunner().invoke(
            apply_cmd, ["42"], obj=_make_app(store, master_resume)
        )

        assert result.exit_code == 0, result.output
        assert "Application recorded for 42" in result.output
        assert notice in result.output
        store.compute_reapply_notice.assert_awaited_once_with(job_id="42")

    def test_no_notice_when_store_returns_none(self, master_resume: Path) -> None:
        store = _make_store(compute_reapply_notice=None)

        result = CliRunner().invoke(
            apply_cmd, ["42"], obj=_make_app(store, master_resume)
        )

        assert result.exit_code == 0, result.output
        assert "Active application" not in result.output

    def test_already_applied_skips_notice(self, master_resume: Path) -> None:
        store = _make_store(
            record_application_with_snapshots=False,
            compute_reapply_notice="should not appear",
        )

        result = CliRunner().invoke(
            apply_cmd, ["42"], obj=_make_app(store, master_resume)
        )

        assert result.exit_code == 0, result.output
        assert "Already applied to 42" in result.output
        store.compute_reapply_notice.assert_not_awaited()


# ---------------------------------------------------------------------------
# apply-history
# ---------------------------------------------------------------------------


class TestApplyHistoryResume:
    """Tests for ``apply-history --resume``."""

    def test_resume_prefix_passes_through(self, master_resume: Path) -> None:
        store = _make_store()

        result = CliRunner().invoke(
            apply_history,
            ["--resume", "abc1", "--limit", "10"],
            obj=_make_app(store, master_resume),
        )

        assert result.exit_code == 0, result.output
        store.list_applications.assert_awaited_once_with(
            limit=10, resume_hash_prefix="abc1"
        )

    def test_history_shows_method_and_notes(self, master_resume: Path) -> None:
        store = _make_store(
            list_applications=[_record(application_method="referral", notes="via Sam")]
        )

        result = CliRunner().invoke(
            apply_history, [], obj=_make_app(store, master_resume)
        )

        assert result.exit_code == 0, result.output
        assert "42" in result.output
        assert "referral" in result.output
        assert "via Sam" in result.output
