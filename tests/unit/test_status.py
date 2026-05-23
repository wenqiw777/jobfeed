"""Unit tests for the domain status transition logic."""

from __future__ import annotations

from jobfeed.domain.status import (
    ALLOWED_TRANSITIONS,
    DECAY_SOURCES,
    RESPONSE_STATUSES,
    STATUS_VALUES,
    is_terminal,
    validate_transition,
)

EXPECTED_STATUS_COUNT = 14


def test_status_values_contains_all_14() -> None:
    """STATUS_VALUES should have exactly 14 statuses."""
    assert len(STATUS_VALUES) == EXPECTED_STATUS_COUNT
    assert "new" in STATUS_VALUES
    assert "ghosted" in STATUS_VALUES


def test_allowed_transitions_covers_all_statuses() -> None:
    """Every status must have an entry in ALLOWED_TRANSITIONS."""
    assert set(ALLOWED_TRANSITIONS.keys()) == STATUS_VALUES


def test_valid_transition_scored_to_shortlisted() -> None:
    """scored → shortlisted should be valid without force."""
    assert validate_transition("scored", "shortlisted") is None


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


def test_terminal_statuses_have_empty_transitions() -> None:
    """Terminal statuses should have no outgoing transitions."""
    for status in ("archived", "ignored", "rejected", "offer", "ghosted"):
        assert ALLOWED_TRANSITIONS[status] == frozenset()


def test_transition_graph_matches_design_spec() -> None:
    """ALLOWED_TRANSITIONS must match the design spec graph exactly."""
    assert ALLOWED_TRANSITIONS["new"] == frozenset({"scored"})
    assert ALLOWED_TRANSITIONS["scored"] == frozenset(
        {
            "shortlisted",
            "applied",
            "archived",
            "ignored",
        }
    )
    assert ALLOWED_TRANSITIONS["shortlisted"] == frozenset(
        {
            "applied",
            "archived",
        }
    )
    assert ALLOWED_TRANSITIONS["applied"] == frozenset(
        {
            "oa",
            "hr_call",
            "second_round",
            "final_round",
            "interviewing",
            "rejected",
            "ghosted",
            "offer",
        }
    )
    assert ALLOWED_TRANSITIONS["oa"] == frozenset(
        {
            "hr_call",
            "second_round",
            "final_round",
            "offer",
            "rejected",
            "ghosted",
        }
    )
    assert ALLOWED_TRANSITIONS["hr_call"] == frozenset(
        {
            "second_round",
            "final_round",
            "offer",
            "rejected",
            "ghosted",
        }
    )
    assert ALLOWED_TRANSITIONS["second_round"] == frozenset(
        {
            "final_round",
            "offer",
            "rejected",
            "ghosted",
        }
    )
    assert ALLOWED_TRANSITIONS["final_round"] == frozenset(
        {
            "offer",
            "rejected",
            "ghosted",
        }
    )
    assert ALLOWED_TRANSITIONS["interviewing"] == frozenset(
        {
            "offer",
            "rejected",
            "ghosted",
        }
    )


def test_interview_stages_are_forward_only() -> None:
    """Interview sub-stages cannot rewind (e.g., hr_call → oa)."""
    assert validate_transition("hr_call", "oa") is not None
    assert validate_transition("second_round", "oa") is not None
    assert validate_transition("final_round", "hr_call") is not None
    assert validate_transition("oa", "hr_call") is None


EXPECTED_DECAY = frozenset(
    {
        "applied",
        "interviewing",
        "oa",
        "hr_call",
        "second_round",
        "final_round",
    }
)
EXPECTED_RESPONSE = frozenset(
    {
        "interviewing",
        "oa",
        "hr_call",
        "second_round",
        "final_round",
        "offer",
        "rejected",
    }
)


def test_decay_sources_includes_interview_substages() -> None:
    """DECAY_SOURCES should include all 6 ghostable statuses."""
    assert DECAY_SOURCES == EXPECTED_DECAY


def test_response_statuses_includes_interview_substages() -> None:
    """RESPONSE_STATUSES should include interview sub-stages."""
    assert RESPONSE_STATUSES == EXPECTED_RESPONSE


def test_same_status_transition_with_force() -> None:
    """Re-marking the same status should succeed with force."""
    assert validate_transition("applied", "applied", force=True) is None
