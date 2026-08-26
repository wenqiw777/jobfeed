"""Unit tests for ApplicationService (pure mock, no @postgres)."""

from __future__ import annotations

import ast
import asyncio
import hashlib
import importlib
import pathlib
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from jobfeed.domain.errors import SnapshotAmbiguousError, SnapshotNotFoundError
from jobfeed.domain.models import ApplicationRecord, ApplicationStats, ResumeSnapshot
from jobfeed.domain.models_application import ResumeVariantStats
from jobfeed.services.application import ApplicationService, ApplyRequest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MASTER_TEXT = "Alice Engineer — Software Developer\nPython, Go, SQL"
_TAILORED_TEXT = "Alice Engineer — ML Engineer\nPython, PyTorch, TensorFlow"
_MASTER_HASH = hashlib.sha256(_MASTER_TEXT.encode()).hexdigest()
_TAILORED_HASH = hashlib.sha256(_TAILORED_TEXT.encode()).hexdigest()
_EXPECTED_BOTH_SNAPSHOTS = 2


def _make_store(**overrides: object) -> AsyncMock:
    """Build a mock store with sensible defaults for every method."""
    store = AsyncMock()
    store.record_application_with_snapshots.return_value = overrides.get(
        "record_application_with_snapshots",
        True,
    )
    store.list_applications.return_value = overrides.get(
        "list_applications",
        [],
    )
    store.application_stats.return_value = overrides.get(
        "application_stats",
        ApplicationStats(
            applied_count=0,
            response_count=0,
            interview_count=0,
            offer_count=0,
            rejection_count=0,
        ),
    )
    store.get_resume_snapshot.return_value = overrides.get(
        "get_resume_snapshot",
    )
    return store


def _make_logger() -> MagicMock:
    return MagicMock()


def _svc(store: AsyncMock | None = None) -> ApplicationService:
    return ApplicationService(
        store=store or _make_store(),
        logger=_make_logger(),
    )


def _req(**overrides: object) -> ApplyRequest:
    """Build an ApplyRequest with sensible defaults."""
    defaults: dict[str, object] = {"job_id": "42", "master_resume": _MASTER_TEXT}
    defaults.update(overrides)
    return ApplyRequest(**defaults)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# apply
# ---------------------------------------------------------------------------


class TestApply:
    """Tests for ApplicationService.apply."""

    def test_apply_master_only_returns_true(self) -> None:
        """apply with master resume only should snapshot master and return True."""
        store = _make_store(record_application_with_snapshots=True)
        svc = _svc(store)
        result = asyncio.run(svc.apply(_req()))
        assert result is True
        call_args = store.record_application_with_snapshots.call_args
        record = call_args.args[0]
        assert isinstance(record, ApplicationRecord)
        assert record.job_id == "42"
        assert record.master_resume_hash == _MASTER_HASH
        assert record.tailored_resume_hash is None
        snapshots = call_args.kwargs["snapshots"]
        assert len(snapshots) == 1
        assert snapshots[0].resume_hash == _MASTER_HASH
        assert snapshots[0].source == "master"
        assert snapshots[0].content == _MASTER_TEXT

    def test_apply_with_tailored_snapshots_both(self) -> None:
        """apply with tailored resume should create two snapshots."""
        store = _make_store(record_application_with_snapshots=True)
        svc = _svc(store)
        asyncio.run(svc.apply(_req(tailored_resume=_TAILORED_TEXT)))
        call_args = store.record_application_with_snapshots.call_args
        record = call_args.args[0]
        assert record.tailored_resume_hash == _TAILORED_HASH
        snapshots = call_args.kwargs["snapshots"]
        assert len(snapshots) == _EXPECTED_BOTH_SNAPSHOTS
        sources = {s.source for s in snapshots}
        assert sources == {"master", "tailored"}

    def test_apply_reapply_returns_false(self) -> None:
        """apply on an already-applied job should return False."""
        store = _make_store(record_application_with_snapshots=False)
        svc = _svc(store)
        result = asyncio.run(svc.apply(_req()))
        assert result is False

    def test_apply_passes_variant(self) -> None:
        """variant should be forwarded to the store."""
        store = _make_store()
        svc = _svc(store)
        asyncio.run(svc.apply(_req(variant="ml-focused")))
        call_kwargs = store.record_application_with_snapshots.call_args.kwargs
        assert call_kwargs["resume_variant"] == "ml-focused"

    def test_apply_passes_cover_letter_and_method(self) -> None:
        """cover_letter and application_method should appear on the record."""
        store = _make_store()
        svc = _svc(store)
        asyncio.run(
            svc.apply(
                _req(
                    cover_letter="Dear Hiring Manager...",
                    application_method="website",
                ),
            ),
        )
        record = store.record_application_with_snapshots.call_args.args[0]
        assert record.cover_letter == "Dear Hiring Manager..."
        assert record.application_method == "website"

    def test_apply_passes_notes(self) -> None:
        """notes should appear on the persisted record."""
        store = _make_store()
        svc = _svc(store)
        asyncio.run(svc.apply(_req(notes="via Sam")))
        record = store.record_application_with_snapshots.call_args.args[0]
        assert record.notes == "via Sam"

    def test_apply_without_notes_defaults_none(self) -> None:
        """notes should default to None on the record."""
        store = _make_store()
        svc = _svc(store)
        asyncio.run(svc.apply(_req()))
        record = store.record_application_with_snapshots.call_args.args[0]
        assert record.notes is None

    def test_apply_passes_evaluation_snapshots(self) -> None:
        """verdict/fit/hooks snapshots should appear on the record."""
        store = _make_store()
        svc = _svc(store)
        asyncio.run(
            svc.apply(
                _req(
                    verdict_snapshot="strong yes",
                    fit_snapshot="good fit for ML role",
                    hooks_snapshot="Python expertise",
                ),
            ),
        )
        record = store.record_application_with_snapshots.call_args.args[0]
        assert record.verdict_snapshot == "strong yes"
        assert record.fit_snapshot == "good fit for ML role"
        assert record.hooks_snapshot == "Python expertise"


# ---------------------------------------------------------------------------
# apply_history
# ---------------------------------------------------------------------------


class TestApplyHistory:
    """Tests for ApplicationService.apply_history."""

    def test_apply_history_delegates_to_store(self) -> None:
        """apply_history should forward limit to store.list_applications."""
        store = _make_store()
        svc = _svc(store)
        asyncio.run(svc.apply_history(limit=50))
        store.list_applications.assert_awaited_once_with(
            limit=50,
            resume_hash_prefix=None,
        )

    def test_apply_history_default_limit(self) -> None:
        """apply_history without limit should use 100."""
        store = _make_store()
        svc = _svc(store)
        asyncio.run(svc.apply_history())
        store.list_applications.assert_awaited_once_with(
            limit=100,
            resume_hash_prefix=None,
        )

    def test_apply_history_passes_resume_hash_prefix(self) -> None:
        """apply_history should forward the resume hash prefix filter."""
        store = _make_store()
        svc = _svc(store)
        asyncio.run(svc.apply_history(limit=10, resume_hash_prefix="abc1"))
        store.list_applications.assert_awaited_once_with(
            limit=10,
            resume_hash_prefix="abc1",
        )


# ---------------------------------------------------------------------------
# reapply_notice
# ---------------------------------------------------------------------------


class TestReapplyNotice:
    """Tests for ApplicationService.reapply_notice."""

    def test_reapply_notice_delegates_to_store(self) -> None:
        """reapply_notice should forward job_id to compute_reapply_notice."""
        store = _make_store()
        store.compute_reapply_notice.return_value = "Active application at acme"
        svc = _svc(store)
        result = asyncio.run(svc.reapply_notice("42"))
        assert result == "Active application at acme"
        store.compute_reapply_notice.assert_awaited_once_with(job_id="42")

    def test_reapply_notice_none_passthrough(self) -> None:
        """A None store result should pass through unchanged."""
        store = _make_store()
        store.compute_reapply_notice.return_value = None
        svc = _svc(store)
        assert asyncio.run(svc.reapply_notice("42")) is None


# ---------------------------------------------------------------------------
# stats
# ---------------------------------------------------------------------------


class TestStats:
    """Tests for ApplicationService.stats."""

    def test_stats_delegates_to_store(self) -> None:
        """stats should forward params to store.application_stats."""
        store = _make_store()
        svc = _svc(store)
        asyncio.run(svc.stats(since_days_ago=7, by_resume=True))
        store.application_stats.assert_awaited_once_with(
            since_days_ago=7,
            by_resume=True,
        )

    def test_stats_by_resume_returns_variant_breakdown(self) -> None:
        """by_resume=True should return per-variant stats."""
        expected = ApplicationStats(
            applied_count=5,
            response_count=2,
            interview_count=1,
            offer_count=0,
            rejection_count=1,
            by_resume={
                "ml-focused": ResumeVariantStats(
                    sent=3,
                    responses=1,
                    interviews=1,
                    offers=0,
                    rejections=0,
                ),
            },
        )
        store = _make_store(application_stats=expected)
        svc = _svc(store)
        result = asyncio.run(svc.stats(by_resume=True))
        assert result is expected
        assert result.by_resume is not None
        assert "ml-focused" in result.by_resume

    def test_stats_empty_db_returns_zeroed(self) -> None:
        """Empty database should return zeroed stats."""
        expected = ApplicationStats(
            applied_count=0,
            response_count=0,
            interview_count=0,
            offer_count=0,
            rejection_count=0,
        )
        store = _make_store(application_stats=expected)
        svc = _svc(store)
        result = asyncio.run(svc.stats())
        assert result.applied_count == 0
        assert result.offer_count == 0
        assert result.by_resume is None


# ---------------------------------------------------------------------------
# get_snapshot
# ---------------------------------------------------------------------------


class TestGetSnapshot:
    """Tests for ApplicationService.get_snapshot."""

    def test_get_snapshot_returns_snapshot(self) -> None:
        """get_snapshot should return the snapshot from the store."""
        snap = ResumeSnapshot(
            resume_hash=_MASTER_HASH,
            captured_at=datetime.now(UTC),
            source="master",
            content=_MASTER_TEXT,
        )
        store = _make_store(get_resume_snapshot=snap)
        svc = _svc(store)
        result = asyncio.run(svc.get_snapshot(_MASTER_HASH))
        assert result is snap

    def test_get_snapshot_not_found_returns_none(self) -> None:
        """get_snapshot should return None when the hash is unknown."""
        store = _make_store()
        svc = _svc(store)
        result = asyncio.run(svc.get_snapshot("deadbeef" * 8))
        assert result is None


# ---------------------------------------------------------------------------
# diff_snapshots
# ---------------------------------------------------------------------------


class TestDiffSnapshots:
    """Tests for ApplicationService.diff_snapshots."""

    def test_diff_snapshots_returns_unified_diff(self) -> None:
        """diff_snapshots should produce a unified diff of two snapshots."""
        now = datetime.now(UTC)
        snap_a = ResumeSnapshot(
            resume_hash=_MASTER_HASH,
            captured_at=now,
            source="master",
            content="line one\nline two\n",
        )
        snap_b = ResumeSnapshot(
            resume_hash=_TAILORED_HASH,
            captured_at=now,
            source="tailored",
            content="line one\nline three\n",
        )

        store = AsyncMock()

        async def _get_snap(prefix: str) -> ResumeSnapshot:
            if _MASTER_HASH.startswith(prefix):
                return snap_a
            if _TAILORED_HASH.startswith(prefix):
                return snap_b
            raise SnapshotNotFoundError(f"no resume snapshot matches prefix {prefix!r}")

        store.get_resume_snapshot_by_prefix.side_effect = _get_snap
        svc = ApplicationService(store=store, logger=_make_logger())
        result = asyncio.run(svc.diff_snapshots(_MASTER_HASH[:12], _TAILORED_HASH[:12]))
        assert "--- " in result
        assert "+++ " in result
        # Labels carry the full resolved hashes, not the input prefixes.
        assert _MASTER_HASH in result
        assert _TAILORED_HASH in result
        assert "-line two" in result
        assert "+line three" in result

    def test_diff_snapshots_missing_prefix_propagates(self) -> None:
        """diff_snapshots should propagate SnapshotNotFoundError."""
        store = _make_store()
        store.get_resume_snapshot_by_prefix.side_effect = SnapshotNotFoundError(
            "no resume snapshot matches prefix 'aaa'"
        )
        svc = _svc(store)
        with pytest.raises(SnapshotNotFoundError, match="matches prefix"):
            asyncio.run(svc.diff_snapshots("aaa", "bbb"))

    def test_diff_snapshots_ambiguous_prefix_propagates(self) -> None:
        """diff_snapshots should propagate SnapshotAmbiguousError."""
        store = _make_store()
        store.get_resume_snapshot_by_prefix.side_effect = SnapshotAmbiguousError(
            "resume hash prefix 'a' matches multiple snapshots"
        )
        svc = _svc(store)
        with pytest.raises(SnapshotAmbiguousError, match="multiple snapshots"):
            asyncio.run(svc.diff_snapshots("a", "b"))


# ---------------------------------------------------------------------------
# Architecture boundary: imports neither adapters nor config
# ---------------------------------------------------------------------------


class TestBoundary:
    """Module-level import boundary enforcement."""

    def test_application_service_imports_no_adapters_or_config(self) -> None:
        """application.py must not import jobfeed.adapters or jobfeed.config."""
        mod_path = pathlib.Path(
            importlib.util.find_spec(
                "jobfeed.services.application",
            ).origin,
        )
        source = mod_path.read_text()
        tree = ast.parse(source)
        forbidden = {"jobfeed.adapters", "jobfeed.config"}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    for f in forbidden:
                        assert not alias.name.startswith(f), (
                            f"application.py imports {alias.name}"
                        )
            if isinstance(node, ast.ImportFrom) and node.module is not None:
                for f in forbidden:
                    assert not node.module.startswith(f), (
                        f"application.py imports from {node.module}"
                    )
