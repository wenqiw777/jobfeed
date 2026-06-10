"""Unit tests for the snapshots CLI (Phase 7, Task 8).

Offline coverage: the store is an AsyncMock injected as the Click context
object. DB-backed prefix resolution lands in the e2e parity suite.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from click.testing import CliRunner

from jobfeed.cli.snapshots import snapshots
from jobfeed.config import Settings
from jobfeed.domain.errors import SnapshotAmbiguousError, SnapshotNotFoundError
from jobfeed.domain.models import ResumeSnapshot
from jobfeed.domain.models_application import ResumeSnapshotSummary

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_CAPTURED_AT = datetime(2026, 5, 21, 12, 0, tzinfo=UTC)
_MASTER_HASH = "aa11" * 16
_TAILORED_HASH = "bb22" * 16


def _make_store() -> AsyncMock:
    store = AsyncMock()
    store.list_resume_snapshots.return_value = []
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


def _summary(
    resume_hash: str,
    source: str = "master",
    usage_count: int = 0,
) -> ResumeSnapshotSummary:
    return ResumeSnapshotSummary(
        resume_hash=resume_hash,
        captured_at=_CAPTURED_AT,
        source=source,
        usage_count=usage_count,
    )


def _snapshot(resume_hash: str, content: str) -> ResumeSnapshot:
    return ResumeSnapshot(
        resume_hash=resume_hash,
        captured_at=_CAPTURED_AT,
        source="master",
        content=content,
    )


# ---------------------------------------------------------------------------
# snapshots list (global mode)
# ---------------------------------------------------------------------------


class TestSnapshotsListGlobal:
    """Tests for ``snapshots list`` without a JOB_ID."""

    def test_lists_hash_date_source_and_usage(self) -> None:
        """Global list shows short hash, captured date, source, used=N."""
        store = _make_store()
        store.list_resume_snapshots.return_value = [
            _summary(_MASTER_HASH, source="master", usage_count=2),
            _summary(_TAILORED_HASH, source="tailored", usage_count=1),
        ]

        result = CliRunner().invoke(snapshots, ["list"], obj=_make_app(store))

        assert result.exit_code == 0, result.output
        store.list_resume_snapshots.assert_awaited_once_with(source=None)
        assert _MASTER_HASH[:12] in result.output
        assert _MASTER_HASH not in result.output  # truncated, not full
        assert "2026-05-21" in result.output
        assert "master" in result.output
        assert "tailored" in result.output
        assert "used=2" in result.output
        assert "used=1" in result.output

    def test_source_filter_is_forwarded(self) -> None:
        """--source tailored is passed through to the store."""
        store = _make_store()

        result = CliRunner().invoke(
            snapshots, ["list", "--source", "tailored"], obj=_make_app(store)
        )

        assert result.exit_code == 0, result.output
        store.list_resume_snapshots.assert_awaited_once_with(source="tailored")

    def test_rejects_unknown_source(self) -> None:
        """--source only accepts master|tailored."""
        store = _make_store()

        result = CliRunner().invoke(
            snapshots, ["list", "--source", "bogus"], obj=_make_app(store)
        )

        assert result.exit_code != 0
        store.list_resume_snapshots.assert_not_awaited()

    def test_empty_list_prints_notice(self) -> None:
        """An empty global list says so and exits 0."""
        store = _make_store()

        result = CliRunner().invoke(snapshots, ["list"], obj=_make_app(store))

        assert result.exit_code == 0, result.output
        assert "No snapshots found." in result.output


# ---------------------------------------------------------------------------
# snapshots list (per-job mode)
# ---------------------------------------------------------------------------


class TestSnapshotsListPerJob:
    """Tests for ``snapshots list JOB_ID`` (unchanged behavior)."""

    def test_shows_application_hashes(self) -> None:
        """Per-job mode prints applied timestamp and both hashes."""
        store = _make_store()
        record = MagicMock()
        record.applied_at = _CAPTURED_AT
        record.master_resume_hash = _MASTER_HASH
        record.tailored_resume_hash = _TAILORED_HASH
        store.get_application.return_value = record

        result = CliRunner().invoke(snapshots, ["list", "42"], obj=_make_app(store))

        assert result.exit_code == 0, result.output
        store.get_application.assert_awaited_once_with("42")
        store.list_resume_snapshots.assert_not_awaited()
        assert "applied: 2026-05-21 12:00" in result.output
        assert f"master:   {_MASTER_HASH}" in result.output
        assert f"tailored: {_TAILORED_HASH}" in result.output

    def test_missing_application_fails(self) -> None:
        """Unknown job id keeps the existing nonzero error."""
        store = _make_store()
        store.get_application.return_value = None

        result = CliRunner().invoke(snapshots, ["list", "42"], obj=_make_app(store))

        assert result.exit_code != 0
        assert "no application found for job 42" in result.output

    def test_source_with_job_id_is_usage_error(self) -> None:
        """--source applies to the global list only."""
        store = _make_store()

        result = CliRunner().invoke(
            snapshots, ["list", "42", "--source", "master"], obj=_make_app(store)
        )

        assert result.exit_code != 0
        assert "--source" in result.output
        store.get_application.assert_not_awaited()


# ---------------------------------------------------------------------------
# snapshots show
# ---------------------------------------------------------------------------


class TestSnapshotsShow:
    """Tests for ``snapshots show <prefix>``."""

    def test_show_resolves_prefix(self) -> None:
        """show prints the content of the prefix-resolved snapshot."""
        store = _make_store()
        store.get_resume_snapshot_by_prefix.return_value = _snapshot(
            _MASTER_HASH, "Resume body."
        )

        result = CliRunner().invoke(
            snapshots, ["show", _MASTER_HASH[:8]], obj=_make_app(store)
        )

        assert result.exit_code == 0, result.output
        store.get_resume_snapshot_by_prefix.assert_awaited_once_with(_MASTER_HASH[:8])
        assert "Resume body." in result.output

    def test_unknown_prefix_message(self) -> None:
        """Unknown prefix surfaces the not-found message, nonzero exit."""
        store = _make_store()
        store.get_resume_snapshot_by_prefix.side_effect = SnapshotNotFoundError(
            "no resume snapshot matches prefix 'zz'"
        )

        result = CliRunner().invoke(snapshots, ["show", "zz"], obj=_make_app(store))

        assert result.exit_code != 0
        assert "no resume snapshot matches prefix 'zz'" in result.output

    def test_ambiguous_prefix_message_is_distinct(self) -> None:
        """Ambiguous prefix gets its own message with a usable hint."""
        store = _make_store()
        store.get_resume_snapshot_by_prefix.side_effect = SnapshotAmbiguousError(
            "resume hash prefix 'a' matches multiple snapshots"
        )

        result = CliRunner().invoke(snapshots, ["show", "a"], obj=_make_app(store))

        assert result.exit_code != 0
        assert "matches multiple snapshots" in result.output
        assert "use more characters" in result.output
        assert "no resume snapshot matches" not in result.output


# ---------------------------------------------------------------------------
# snapshots diff
# ---------------------------------------------------------------------------


class TestSnapshotsDiff:
    """Tests for ``snapshots diff <prefix_a> <prefix_b>``."""

    def test_diff_resolves_both_prefixes(self) -> None:
        """diff resolves each argument by prefix and prints a unified diff."""
        store = _make_store()
        by_hash = {
            _MASTER_HASH[:8]: _snapshot(_MASTER_HASH, "line one\nline two\n"),
            _TAILORED_HASH[:8]: _snapshot(_TAILORED_HASH, "line one\nline three\n"),
        }

        async def _resolve(prefix: str) -> ResumeSnapshot:
            return by_hash[prefix]

        store.get_resume_snapshot_by_prefix.side_effect = _resolve

        result = CliRunner().invoke(
            snapshots,
            ["diff", _MASTER_HASH[:8], _TAILORED_HASH[:8]],
            obj=_make_app(store),
        )

        assert result.exit_code == 0, result.output
        assert "-line two" in result.output
        assert "+line three" in result.output

    def test_diff_identical_snapshots(self) -> None:
        """Identical contents report equality instead of an empty diff."""
        store = _make_store()
        store.get_resume_snapshot_by_prefix.return_value = _snapshot(
            _MASTER_HASH, "same\n"
        )

        result = CliRunner().invoke(
            snapshots,
            ["diff", _MASTER_HASH[:8], _MASTER_HASH[:8]],
            obj=_make_app(store),
        )

        assert result.exit_code == 0, result.output
        assert "Snapshots are identical." in result.output

    def test_diff_unknown_prefix_fails_distinctly(self) -> None:
        """A missing operand surfaces the not-found message."""
        store = _make_store()
        store.get_resume_snapshot_by_prefix.side_effect = SnapshotNotFoundError(
            "no resume snapshot matches prefix 'zz'"
        )

        result = CliRunner().invoke(
            snapshots, ["diff", "zz", "aa"], obj=_make_app(store)
        )

        assert result.exit_code != 0
        assert "no resume snapshot matches prefix 'zz'" in result.output

    def test_diff_ambiguous_prefix_fails_distinctly(self) -> None:
        """An ambiguous operand surfaces the ambiguity message."""
        store = _make_store()
        store.get_resume_snapshot_by_prefix.side_effect = SnapshotAmbiguousError(
            "resume hash prefix 'a' matches multiple snapshots"
        )

        result = CliRunner().invoke(
            snapshots, ["diff", "a", "bb"], obj=_make_app(store)
        )

        assert result.exit_code != 0
        assert "matches multiple snapshots" in result.output
        assert "use more characters" in result.output
