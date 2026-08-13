"""Unit tests for WorkflowService (pure mock, no @postgres)."""

from __future__ import annotations

import ast
import asyncio
import importlib
import pathlib
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from jobfeed.domain.interview import InterviewRound
from jobfeed.domain.models_status import (
    AutoDecayResult,
    BulkResult,
    BulkTransitionRequest,
    TransitionRequest,
    WorkflowAttention,
)
from jobfeed.domain.status import REASON_BULK_CASCADE, REASON_BULK_SELECTED
from jobfeed.services.workflow import WorkflowService

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@dataclass
class _FakeStatusInfo:
    """Minimal stand-in for StatusInfo returned by get_status."""

    status: str


def _make_store(**overrides: Any) -> AsyncMock:
    """Build a mock store with sensible defaults for every method."""
    store = AsyncMock()
    store.transition_status.return_value = overrides.get(
        "transition_status",
        "applied",
    )
    store.get_status.return_value = overrides.get(
        "get_status",
        _FakeStatusInfo(status="applied"),
    )
    store.get_status_history.return_value = overrides.get(
        "get_status_history",
        ["applied", "scored"],
    )
    store.append_note.return_value = None
    store.expand_twin_ids.return_value = overrides.get("expand_twin_ids", {})
    store.transition_status_bulk.return_value = overrides.get(
        "transition_status_bulk",
        BulkResult(succeeded=1, failed=[], skipped=0),
    )
    store.workflow_attention.return_value = overrides.get(
        "workflow_attention",
        WorkflowAttention(
            follow_up_today=[],
            interview_prep=[],
            going_ghosted=[],
        ),
    )
    store.auto_decay.return_value = overrides.get(
        "auto_decay",
        AutoDecayResult(ghosted=0, archived=0),
    )
    store.add_interview_round.return_value = overrides.get(
        "add_interview_round",
        InterviewRound(job_id=1, round_index=1, label="Phone Screen"),
    )
    store.list_interview_rounds.return_value = overrides.get(
        "list_interview_rounds",
        [],
    )
    store.complete_interview_round.return_value = overrides.get(
        "complete_interview_round",
        InterviewRound(
            job_id=1,
            round_index=1,
            label="Phone Screen",
            completed_at=datetime(2026, 1, 1),
        ),
    )
    return store


def _make_logger() -> MagicMock:
    return MagicMock()


def _svc(store: AsyncMock | None = None) -> WorkflowService:
    return WorkflowService(
        store=store or _make_store(),
        logger=_make_logger(),
    )


# ---------------------------------------------------------------------------
# transition
# ---------------------------------------------------------------------------


class TestTransition:
    """Tests for WorkflowService.transition."""

    def test_transition_delegates_to_store(self) -> None:
        """transition should call store.transition_status and return result."""
        store = _make_store(transition_status="interviewing")
        svc = _svc(store)
        result = asyncio.run(
            svc.transition(TransitionRequest(job_id="42", new_status="interviewing")),
        )
        assert result == "interviewing"
        store.transition_status.assert_awaited_once_with(
            TransitionRequest(
                job_id="42",
                new_status="interviewing",
                force=False,
                i_mean_it=False,
                resume_variant=None,
            )
        )

    def test_transition_with_note_appends(self) -> None:
        """When a note is provided, append_note should be called after transition."""
        store = _make_store(transition_status="applied")
        svc = _svc(store)
        asyncio.run(
            svc.transition(
                TransitionRequest(job_id="42", new_status="applied"),
                note="sent follow-up",
            ),
        )
        store.append_note.assert_awaited_once_with(
            job_id="42",
            text="sent follow-up",
        )

    def test_transition_without_note_skips_append(self) -> None:
        """Without a note, append_note should not be called."""
        store = _make_store()
        svc = _svc(store)
        asyncio.run(
            svc.transition(TransitionRequest(job_id="42", new_status="applied")),
        )
        store.append_note.assert_not_awaited()

    def test_transition_passes_force_and_i_mean_it(self) -> None:
        """force and i_mean_it flags should be forwarded to the store."""
        store = _make_store(transition_status="new")
        svc = _svc(store)
        asyncio.run(
            svc.transition(
                TransitionRequest(
                    job_id="42",
                    new_status="new",
                    force=True,
                    i_mean_it=True,
                )
            ),
        )
        store.transition_status.assert_awaited_once_with(
            TransitionRequest(
                job_id="42",
                new_status="new",
                force=True,
                i_mean_it=True,
                resume_variant=None,
            )
        )

    def test_transition_raises_when_store_raises(self) -> None:
        """If the store rejects the transition, the error should propagate."""
        store = _make_store()
        store.transition_status.side_effect = ValueError(
            "transition not allowed",
        )
        svc = _svc(store)
        with pytest.raises(ValueError, match="transition not allowed"):
            asyncio.run(
                svc.transition(TransitionRequest(job_id="42", new_status="offer")),
            )

    def test_transition_passes_resume_variant(self) -> None:
        """resume_variant should be forwarded to the store."""
        store = _make_store(transition_status="applied")
        svc = _svc(store)
        asyncio.run(
            svc.transition(
                TransitionRequest(
                    job_id="42",
                    new_status="applied",
                    resume_variant="ml-focused",
                )
            ),
        )
        store.transition_status.assert_awaited_once_with(
            TransitionRequest(
                job_id="42",
                new_status="applied",
                force=False,
                i_mean_it=False,
                resume_variant="ml-focused",
            )
        )


# ---------------------------------------------------------------------------
# transition_bulk
# ---------------------------------------------------------------------------


class TestTransitionBulk:
    """Tests for WorkflowService.transition_bulk."""

    def test_bulk_delegates_twin_expansion_to_aggregate(self) -> None:
        """The service calls only the public bulk aggregate capability."""
        store = _make_store()
        store.expand_twin_ids.side_effect = AssertionError(
            "expand_twin_ids is an adapter-private helper"
        )
        svc = _svc(store)
        items = [("1", "rejected"), ("2", "archived")]
        asyncio.run(
            svc.transition_bulk(items),
        )
        store.expand_twin_ids.assert_not_awaited()
        store.transition_status_bulk.assert_awaited_once_with(
            BulkTransitionRequest(
                items=items,
                reason_selected=REASON_BULK_SELECTED,
                reason_cascade=REASON_BULK_CASCADE,
                force=False,
                i_mean_it=False,
            )
        )

    def test_bulk_returns_bulk_result(self) -> None:
        """The BulkResult from the store should be returned unchanged."""
        expected = BulkResult(succeeded=3, failed=[("99", "err")], skipped=1)
        store = _make_store(transition_status_bulk=expected)
        svc = _svc(store)
        result = asyncio.run(
            svc.transition_bulk([("1", "rejected")]),
        )
        assert result is expected


# ---------------------------------------------------------------------------
# restore
# ---------------------------------------------------------------------------


class TestRestore:
    """Tests for WorkflowService.restore."""

    def test_restore_picks_non_terminal(self) -> None:
        """restore should use pick_restore_target to find the right status."""
        store = _make_store(
            get_status=_FakeStatusInfo(status="ghosted"),
            get_status_history=["ghosted", "interviewing", "applied"],
            transition_status="interviewing",
        )
        svc = _svc(store)
        result = asyncio.run(
            svc.restore("42"),
        )
        assert result == "interviewing"
        store.transition_status.assert_awaited_once_with(
            TransitionRequest(
                job_id="42",
                new_status="interviewing",
                force=True,
                i_mean_it=True,
                reason="restore",
            )
        )

    def test_restore_falls_back_to_applied(self) -> None:
        """When all history is terminal, restore should fall back to applied."""
        store = _make_store(
            get_status=_FakeStatusInfo(status="ghosted"),
            get_status_history=["ghosted", "archived"],
            transition_status="applied",
        )
        svc = _svc(store)
        result = asyncio.run(
            svc.restore("42"),
        )
        assert result == "applied"
        store.transition_status.assert_awaited_once_with(
            TransitionRequest(
                job_id="42",
                new_status="applied",
                force=True,
                i_mean_it=True,
                reason="restore",
            )
        )

    def test_restore_empty_history_falls_back(self) -> None:
        """Empty history should also fall back to applied."""
        store = _make_store(
            get_status=_FakeStatusInfo(status="ghosted"),
            get_status_history=[],
            transition_status="applied",
        )
        svc = _svc(store)
        result = asyncio.run(
            svc.restore("42"),
        )
        assert result == "applied"

    def test_restore_rejects_non_terminal_status(self) -> None:
        """restore should reject jobs not in ghosted or archived status."""
        store = _make_store(get_status=_FakeStatusInfo(status="applied"))
        svc = _svc(store)
        with pytest.raises(ValueError, match="requires ghosted or archived"):
            asyncio.run(svc.restore("42"))

    def test_restore_accepts_ghosted(self) -> None:
        """restore should accept ghosted status."""
        store = _make_store(
            get_status=_FakeStatusInfo(status="ghosted"),
            get_status_history=["ghosted", "interviewing"],
            transition_status="interviewing",
        )
        svc = _svc(store)
        result = asyncio.run(svc.restore("42"))
        assert result == "interviewing"

    def test_restore_accepts_archived(self) -> None:
        """restore should accept archived status."""
        store = _make_store(
            get_status=_FakeStatusInfo(status="archived"),
            get_status_history=["archived", "applied"],
            transition_status="applied",
        )
        svc = _svc(store)
        result = asyncio.run(svc.restore("42"))
        assert result == "applied"

    def test_restore_rejects_none_status(self) -> None:
        """restore should reject when no status info is found."""
        store = _make_store(get_status=None)
        svc = _svc(store)
        with pytest.raises(ValueError, match="requires ghosted or archived"):
            asyncio.run(svc.restore("42"))


# ---------------------------------------------------------------------------
# add_round
# ---------------------------------------------------------------------------


class TestAddRound:
    """Tests for WorkflowService.add_round."""

    def test_add_round_auto_transitions_from_applied(self) -> None:
        """When the job is 'applied', it should auto-transition to 'interviewing'."""
        store = _make_store(get_status=_FakeStatusInfo(status="applied"))
        svc = _svc(store)
        asyncio.run(
            svc.add_round("42", "Phone Screen"),
        )
        # Should have called transition_status to move to interviewing.
        store.transition_status.assert_any_await(
            TransitionRequest(job_id="42", new_status="interviewing")
        )
        store.add_interview_round.assert_awaited_once_with(
            job_id="42",
            label="Phone Screen",
            scheduled_at=None,
        )

    def test_add_round_stays_interviewing(self) -> None:
        """When already 'interviewing', no extra transition should happen."""
        store = _make_store(get_status=_FakeStatusInfo(status="interviewing"))
        svc = _svc(store)
        asyncio.run(
            svc.add_round("42", "Technical"),
        )
        # transition_status should NOT have been called.
        store.transition_status.assert_not_awaited()
        store.add_interview_round.assert_awaited_once()

    def test_add_round_passes_scheduled_at(self) -> None:
        """scheduled_at should be forwarded to the store."""
        store = _make_store(get_status=_FakeStatusInfo(status="interviewing"))
        ts = datetime(2026, 7, 1, 10, 0)
        svc = _svc(store)
        asyncio.run(
            svc.add_round("42", "Final", scheduled_at=ts),
        )
        store.add_interview_round.assert_awaited_once_with(
            job_id="42",
            label="Final",
            scheduled_at=ts,
        )

    def test_add_round_returns_created_round(self) -> None:
        """The InterviewRound from the store should be returned."""
        expected = InterviewRound(job_id=1, round_index=2, label="Behavioral")
        store = _make_store(
            get_status=_FakeStatusInfo(status="interviewing"),
            add_interview_round=expected,
        )
        svc = _svc(store)
        result = asyncio.run(
            svc.add_round("42", "Behavioral"),
        )
        assert result is expected


# ---------------------------------------------------------------------------
# note
# ---------------------------------------------------------------------------


class TestNote:
    """Tests for WorkflowService.note."""

    def test_note_delegates_to_store(self) -> None:
        """note should call store.append_note."""
        store = _make_store()
        svc = _svc(store)
        asyncio.run(
            svc.note("42", "recruiter emailed back"),
        )
        store.append_note.assert_awaited_once_with(
            job_id="42",
            text="recruiter emailed back",
        )


# ---------------------------------------------------------------------------
# set_followup
# ---------------------------------------------------------------------------


class TestSetFollowup:
    """Tests for WorkflowService.set_followup."""

    def test_set_followup_delegates_to_store(self) -> None:
        """set_followup should forward job_id and at to the store."""
        store = _make_store()
        store.set_followup.return_value = True
        svc = _svc(store)
        at = datetime(2026, 6, 17, tzinfo=UTC)
        result = asyncio.run(svc.set_followup(job_id="42", at=at))
        assert result is True
        store.set_followup.assert_awaited_once_with(job_id="42", at=at)

    def test_set_followup_missing_job_returns_false(self) -> None:
        """A store miss (no status row) should surface as False."""
        store = _make_store()
        store.set_followup.return_value = False
        svc = _svc(store)
        at = datetime(2026, 6, 17, tzinfo=UTC)
        result = asyncio.run(svc.set_followup(job_id="999", at=at))
        assert result is False


# ---------------------------------------------------------------------------
# attention / auto_decay pass-throughs
# ---------------------------------------------------------------------------


class TestAttention:
    """Tests for WorkflowService.attention."""

    def test_attention_delegates_to_store(self) -> None:
        """attention should forward params to store.workflow_attention."""
        store = _make_store()
        svc = _svc(store)
        asyncio.run(
            svc.attention(auto_ghost_days=15, lookahead_days=3),
        )
        store.workflow_attention.assert_awaited_once_with(
            auto_ghost_days=15,
            lookahead_days=3,
        )


class TestAutoDecay:
    """Tests for WorkflowService.auto_decay."""

    def test_auto_decay_delegates_to_store(self) -> None:
        """auto_decay should forward params to store.auto_decay."""
        expected = AutoDecayResult(ghosted=2, archived=5)
        store = _make_store(auto_decay=expected)
        svc = _svc(store)
        result = asyncio.run(
            svc.auto_decay(ghost_days=20, archive_ignored_days=7),
        )
        assert result is expected
        store.auto_decay.assert_awaited_once_with(
            ghost_days=20,
            archive_ignored_days=7,
        )


# ---------------------------------------------------------------------------
# complete_round / list_rounds pass-throughs
# ---------------------------------------------------------------------------


class TestCompleteRound:
    """Tests for WorkflowService.complete_round."""

    def test_complete_round_delegates(self) -> None:
        """complete_round should forward to store.complete_interview_round."""
        store = _make_store()
        svc = _svc(store)
        asyncio.run(
            svc.complete_round("42", round_index=1, notes="went well"),
        )
        store.complete_interview_round.assert_awaited_once_with(
            job_id="42",
            round_index=1,
            notes="went well",
        )


class TestListRounds:
    """Tests for WorkflowService.list_rounds."""

    def test_list_rounds_delegates(self) -> None:
        """list_rounds should forward to store.list_interview_rounds."""
        store = _make_store()
        svc = _svc(store)
        asyncio.run(svc.list_rounds("42"))
        store.list_interview_rounds.assert_awaited_once_with("42")


# ---------------------------------------------------------------------------
# Architecture boundary: imports neither adapters nor config
# ---------------------------------------------------------------------------


class TestBoundary:
    """Module-level import boundary enforcement."""

    def test_workflow_service_imports_no_adapters_or_config(self) -> None:
        """workflow.py must not import jobfeed.adapters or jobfeed.config."""
        mod_path = pathlib.Path(
            importlib.util.find_spec(
                "jobfeed.services.workflow",
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
                            f"workflow.py imports {alias.name}"
                        )
            if isinstance(node, ast.ImportFrom) and node.module is not None:
                for f in forbidden:
                    assert not node.module.startswith(f), (
                        f"workflow.py imports from {node.module}"
                    )
