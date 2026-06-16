"""Unit tests for the Phase 6 status transition graph."""

from __future__ import annotations

from jobfeed.domain.status import (
    ACTIVE_APPLICATION_STATUSES,
    ALLOWED_TRANSITIONS,
    DECAY_SOURCES,
    REASON_BULK_CASCADE,
    REASON_BULK_SELECTED,
    RESPONSE_STATUSES,
    STATUS_VALUES,
    is_terminal,
    pick_restore_target,
    validate_transition,
)

EXPECTED_STATUS_COUNT = 11


def test_status_values_has_11_statuses() -> None:
    """STATUS_VALUES should have exactly 11 statuses after Phase 6."""
    assert len(STATUS_VALUES) == EXPECTED_STATUS_COUNT


def test_status_values_contains_all_expected() -> None:
    """Every expected status must be present."""
    expected = {
        "new",
        "scored",
        "shortlisted",
        "awaiting_referral",
        "applied",
        "interviewing",
        "offer",
        "rejected",
        "ghosted",
        "archived",
        "ignored",
    }
    assert expected == STATUS_VALUES


def test_retired_statuses_removed() -> None:
    """The four retired interview sub-statuses must not be in STATUS_VALUES."""
    for retired in ("oa", "hr_call", "second_round", "final_round"):
        assert retired not in STATUS_VALUES


def test_allowed_transitions_covers_all_statuses() -> None:
    """Every status must have an entry in ALLOWED_TRANSITIONS."""
    assert set(ALLOWED_TRANSITIONS.keys()) == STATUS_VALUES


# --- Phase 6 graph edges ---


def test_new_transitions() -> None:
    """new can only transition to scored."""
    assert ALLOWED_TRANSITIONS["new"] == frozenset({"scored"})


def test_scored_transitions() -> None:
    """scored fans out to shortlisted, awaiting_referral, applied, archived, ignored."""
    assert ALLOWED_TRANSITIONS["scored"] == frozenset(
        {"shortlisted", "awaiting_referral", "applied", "archived", "ignored"},
    )


def test_shortlisted_transitions() -> None:
    """shortlisted can go to awaiting_referral, applied, or archived."""
    assert ALLOWED_TRANSITIONS["shortlisted"] == frozenset(
        {"awaiting_referral", "applied", "archived"},
    )


def test_awaiting_referral_transitions() -> None:
    """awaiting_referral can go to applied or archived."""
    assert ALLOWED_TRANSITIONS["awaiting_referral"] == frozenset(
        {"applied", "archived"},
    )


def test_applied_transitions() -> None:
    """applied fans out to interviewing, offer, rejected, ghosted, archived."""
    assert ALLOWED_TRANSITIONS["applied"] == frozenset(
        {"interviewing", "offer", "rejected", "ghosted", "archived"},
    )


def test_interviewing_transitions() -> None:
    """interviewing fans out to offer, rejected, ghosted, archived."""
    assert ALLOWED_TRANSITIONS["interviewing"] == frozenset(
        {"offer", "rejected", "ghosted", "archived"},
    )


def test_active_pipeline_jobs_can_be_archived_without_force() -> None:
    """applied/interviewing can be abandoned to archived without force."""
    assert validate_transition("applied", "archived") is None
    assert validate_transition("interviewing", "archived") is None


def test_applied_to_oa_rejected_as_unknown() -> None:
    """applied -> oa must be rejected (oa is no longer a valid status)."""
    error = validate_transition("applied", "oa")
    assert error is not None
    assert "unknown" in error


def test_awaiting_referral_to_ghosted_rejected_without_force() -> None:
    """awaiting_referral -> ghosted is not in the graph; needs force."""
    error = validate_transition("awaiting_referral", "ghosted")
    assert error is not None
    assert "not allowed" in error


def test_awaiting_referral_to_ghosted_with_force() -> None:
    """awaiting_referral -> ghosted succeeds with force."""
    assert validate_transition("awaiting_referral", "ghosted", force=True) is None


# --- Terminal statuses ---


def test_ghosted_is_terminal() -> None:
    """ghosted should be terminal."""
    assert is_terminal("ghosted") is True


def test_archived_is_terminal() -> None:
    """archived should be terminal."""
    assert is_terminal("archived") is True


def test_terminal_statuses_have_empty_transitions() -> None:
    """All five terminal statuses should have no outgoing edges."""
    for status in ("ignored", "archived", "rejected", "offer", "ghosted"):
        assert ALLOWED_TRANSITIONS[status] == frozenset()


def test_non_terminal_statuses() -> None:
    """Non-terminal statuses should not be flagged as terminal."""
    for status in (
        "new",
        "scored",
        "shortlisted",
        "awaiting_referral",
        "applied",
        "interviewing",
    ):
        assert is_terminal(status) is False


# --- Set constants ---


def test_decay_sources() -> None:
    """DECAY_SOURCES should be exactly {applied, interviewing}."""
    assert frozenset({"applied", "interviewing"}) == DECAY_SOURCES


def test_response_statuses() -> None:
    """RESPONSE_STATUSES should be exactly {interviewing, offer, rejected}."""
    assert frozenset({"interviewing", "offer", "rejected"}) == RESPONSE_STATUSES


def test_active_application_statuses_tracks_decay() -> None:
    """ACTIVE_APPLICATION_STATUSES should equal DECAY_SOURCES."""
    assert frozenset({"applied", "interviewing"}) == ACTIVE_APPLICATION_STATUSES
    assert ACTIVE_APPLICATION_STATUSES is DECAY_SOURCES


# --- pick_restore_target ---


def test_pick_restore_target_returns_first_non_terminal() -> None:
    """Should return the first non-terminal status in the history."""
    history = ["ghosted", "interviewing", "applied"]
    assert pick_restore_target(history) == "interviewing"


def test_pick_restore_target_skips_ghosted_archived_only() -> None:
    """Should return None when history has only ghosted/archived."""
    assert pick_restore_target(["ghosted", "archived"]) is None


def test_pick_restore_target_returns_ignored() -> None:
    """An archived-ignored job should restore to ignored, not further back."""
    history = ["archived", "ignored", "scored", "new"]
    assert pick_restore_target(history) == "ignored"


def test_pick_restore_target_returns_rejected() -> None:
    """rejected is a valid restore target (not ghosted/archived)."""
    history = ["ghosted", "rejected", "applied"]
    assert pick_restore_target(history) == "rejected"


def test_pick_restore_target_empty_history() -> None:
    """Should return None for an empty history."""
    assert pick_restore_target([]) is None


def test_pick_restore_target_first_is_non_terminal() -> None:
    """Should return the first entry when it is non-terminal."""
    assert pick_restore_target(["applied", "scored"]) == "applied"


def test_pick_restore_target_maps_retired_to_interviewing() -> None:
    """Retired statuses should map to 'interviewing', not be skipped."""
    history = ["oa", "applied", "scored"]
    assert pick_restore_target(history) == "interviewing"


def test_pick_restore_target_all_retired_maps_to_interviewing() -> None:
    """When history has only retired + terminal, retired should map to interviewing."""
    history = ["hr_call", "oa", "ghosted"]
    assert pick_restore_target(history) == "interviewing"


def test_pick_restore_target_all_four_retired_statuses() -> None:
    """All four retired statuses should map to 'interviewing'."""
    for retired in ("oa", "hr_call", "second_round", "final_round"):
        assert pick_restore_target([retired]) == "interviewing"


# --- Reason constants ---


def test_reason_bulk_selected_value() -> None:
    """REASON_BULK_SELECTED should be 'bulk'."""
    assert REASON_BULK_SELECTED == "bulk"


def test_reason_bulk_cascade_value() -> None:
    """REASON_BULK_CASCADE should be 'bulk-cascade'."""
    assert REASON_BULK_CASCADE == "bulk-cascade"


# --- Force bypass + archived→new double gate ---


def test_force_bypass_allows_arbitrary_transition() -> None:
    """force=True should bypass the transition graph."""
    assert validate_transition("scored", "offer", force=True) is None


def test_archived_to_new_requires_i_mean_it() -> None:
    """archived -> new with force alone should fail; needs i_mean_it too."""
    error = validate_transition("archived", "new", force=True)
    assert error is not None
    assert "i_mean_it" in error

    assert validate_transition("archived", "new", force=True, i_mean_it=True) is None
