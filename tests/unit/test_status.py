"""Unit tests for the domain status transition logic."""

from __future__ import annotations

from jobfeed.domain.status import (
    ALLOWED_TRANSITIONS,
    DECAY_SOURCES,
    LIST_DEFAULT_STATUSES,
    RESPONSE_STATUSES,
    STATUS_VALUES,
    is_terminal,
    validate_transition,
)

EXPECTED_STATUS_COUNT = 11


def test_status_values_contains_all_11() -> None:
    """STATUS_VALUES should have exactly 11 statuses (Phase 6)."""
    assert len(STATUS_VALUES) == EXPECTED_STATUS_COUNT
    assert "new" in STATUS_VALUES
    assert "ghosted" in STATUS_VALUES


def test_allowed_transitions_covers_all_statuses() -> None:
    """Every status must have an entry in ALLOWED_TRANSITIONS."""
    assert set(ALLOWED_TRANSITIONS.keys()) == STATUS_VALUES


def test_list_default_statuses_is_all_minus_new_and_archived() -> None:
    """LIST_DEFAULT_STATUSES is the live status set minus new/archived."""
    expected = STATUS_VALUES - {"new", "archived"}
    assert expected == LIST_DEFAULT_STATUSES
    assert len(LIST_DEFAULT_STATUSES) == EXPECTED_STATUS_COUNT - 2


def test_valid_transition_scored_to_shortlisted() -> None:
    """scored → shortlisted should be valid without force."""
    assert validate_transition("scored", "shortlisted") is None


def test_applied_can_return_to_wait_or_be_ignored() -> None:
    """Applied jobs can return to triage or be dismissed without force."""
    assert validate_transition("applied", "shortlisted") is None
    assert validate_transition("applied", "ignored") is None


def test_invalid_transition_scored_to_offer_without_force() -> None:
    """scored → offer should fail without force."""
    error = validate_transition("scored", "offer")
    assert error is not None
    assert "not allowed" in error


def test_forced_transition_scored_to_offer() -> None:
    """scored → offer should succeed with force=True."""
    assert validate_transition("scored", "offer", force=True) is None


def test_archived_to_new_requires_double_gate() -> None:
    """archived → new needs both force and i_mean_it."""
    error = validate_transition("archived", "new", force=True)
    assert error is not None
    assert "i_mean_it" in error

    assert (
        validate_transition(
            "archived",
            "new",
            force=True,
            i_mean_it=True,
        )
        is None
    )


def test_unknown_status_rejected() -> None:
    """Unknown statuses should produce an error."""
    error = validate_transition("bogus", "new")
    assert error is not None
    assert "unknown" in error


def test_is_terminal_returns_true_for_terminal_statuses() -> None:
    """Terminal statuses should be identified correctly."""
    for status in ("archived", "ignored", "rejected", "offer", "ghosted"):
        assert is_terminal(status) is True


def test_is_terminal_returns_false_for_non_terminal() -> None:
    """Non-terminal statuses should not be flagged."""
    for status in ("new", "scored", "shortlisted", "applied", "interviewing"):
        assert is_terminal(status) is False


def test_terminal_statuses_only_allow_user_decision_corrections() -> None:
    """Workflow-terminal rows remain correctable from the four-state UI."""
    for status in ("archived", "ignored"):
        assert ALLOWED_TRANSITIONS[status] == frozenset(
            {"scored", "shortlisted", "applied"}
        )
    for status in ("rejected", "offer", "ghosted"):
        assert ALLOWED_TRANSITIONS[status] == frozenset(
            {"scored", "shortlisted", "ignored"}
        )


def test_transition_graph_preserves_workflow_and_user_corrections() -> None:
    """The graph includes workflow edges and explicit four-state corrections."""
    assert ALLOWED_TRANSITIONS["new"] == frozenset(
        {"scored", "shortlisted", "applied", "ignored"}
    )
    assert ALLOWED_TRANSITIONS["scored"] == frozenset(
        {"shortlisted", "awaiting_referral", "applied", "archived", "ignored"},
    )
    assert ALLOWED_TRANSITIONS["shortlisted"] == frozenset(
        {"scored", "awaiting_referral", "applied", "archived", "ignored"},
    )
    assert ALLOWED_TRANSITIONS["awaiting_referral"] == frozenset(
        {"scored", "shortlisted", "applied", "archived", "ignored"},
    )
    assert ALLOWED_TRANSITIONS["applied"] == frozenset(
        {
            "shortlisted",
            "scored",
            "interviewing",
            "offer",
            "rejected",
            "ghosted",
            "archived",
            "ignored",
        },
    )
    assert ALLOWED_TRANSITIONS["interviewing"] == frozenset(
        {
            "scored",
            "shortlisted",
            "offer",
            "rejected",
            "ghosted",
            "archived",
            "ignored",
        },
    )


def test_active_pipeline_can_archive_without_force() -> None:
    """A stale applied/interviewing job can be abandoned to archived."""
    assert validate_transition("applied", "archived") is None
    assert validate_transition("interviewing", "archived") is None


def test_awaiting_referral_cannot_reach_ghosted() -> None:
    """awaiting_referral has no edge to ghosted (dead referral → archive)."""
    assert validate_transition("awaiting_referral", "ghosted") is not None
    assert validate_transition("awaiting_referral", "applied") is None


EXPECTED_DECAY = frozenset({"applied", "interviewing"})
EXPECTED_RESPONSE = frozenset({"interviewing", "offer", "rejected"})


def test_decay_sources_matches_phase6() -> None:
    """DECAY_SOURCES should include applied and interviewing only."""
    assert DECAY_SOURCES == EXPECTED_DECAY


def test_response_statuses_matches_phase6() -> None:
    """RESPONSE_STATUSES should include interviewing, offer, rejected."""
    assert RESPONSE_STATUSES == EXPECTED_RESPONSE


def test_same_status_transition_with_force() -> None:
    """Re-marking the same status should succeed with force."""
    assert validate_transition("applied", "applied", force=True) is None
