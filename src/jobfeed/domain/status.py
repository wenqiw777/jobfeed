"""Pure domain logic for job status transitions."""

from __future__ import annotations

STATUS_VALUES: frozenset[str] = frozenset(
    {
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
)

ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    "new": frozenset({"scored"}),
    "scored": frozenset(
        {"shortlisted", "awaiting_referral", "applied", "archived", "ignored"},
    ),
    "shortlisted": frozenset({"awaiting_referral", "applied", "archived"}),
    "awaiting_referral": frozenset({"applied", "archived"}),
    "applied": frozenset({"interviewing", "offer", "rejected", "ghosted"}),
    "interviewing": frozenset({"offer", "rejected", "ghosted"}),
    "ignored": frozenset(),
    "archived": frozenset(),
    "rejected": frozenset(),
    "offer": frozenset(),
    "ghosted": frozenset(),
}

_TERMINAL: frozenset[str] = frozenset(
    {
        "ignored",
        "archived",
        "rejected",
        "offer",
        "ghosted",
    }
)

DECAY_SOURCES: frozenset[str] = frozenset({"applied", "interviewing"})

# Statuses where an application is still active (post-submit, pre-terminal).
# Identical to DECAY_SOURCES: an application is "active" exactly while it remains
# subject to ghost/auto-decay. Used for the same-company reapply guard.
ACTIVE_APPLICATION_STATUSES: frozenset[str] = DECAY_SOURCES

RESPONSE_STATUSES: frozenset[str] = frozenset(
    {"interviewing", "offer", "rejected"},
)

# Default status set for `jobfeed list`, applied at the CLI layer (the store's
# StatusFilter keeps None = no filter). Mirrors legacy's "actionable middle":
# every live status except new and archived.
LIST_DEFAULT_STATUSES: frozenset[str] = STATUS_VALUES - {"new", "archived"}

# Status-decay / follow-up policy defaults. Single source of truth for the
# store and service layers.
DEFAULT_FOLLOWUP_GRACE_DAYS = 7
DEFAULT_GHOST_DAYS = 30
DEFAULT_ARCHIVE_IGNORED_DAYS = 14

# Reason constants for bulk operations.
REASON_BULK_SELECTED = "bulk"
REASON_BULK_CASCADE = "bulk-cascade"

# Reason for the automatic new → scored advance written by the store when a
# Stage A evaluation is persisted. Naming follows "auto_decay".
REASON_AUTO_SCORED = "auto_scored"


def is_terminal(status: str) -> bool:
    """Check whether a status has no allowed outgoing transitions.

    Args:
        status: Status string to check.

    Returns:
        True if the status is terminal.
    """
    return status in _TERMINAL


_RETIRED_TO_LIVE: dict[str, str] = {
    "oa": "interviewing",
    "hr_call": "interviewing",
    "second_round": "interviewing",
    "final_round": "interviewing",
}

_RESTORE_SKIP: frozenset[str] = frozenset({"ghosted", "archived"})


def pick_restore_target(history: list[str]) -> str | None:
    """Find the most recent restorable status from a history.

    Skips only ghosted and archived (the states we are restoring FROM).
    Other terminal statuses like ignored, rejected, offer are valid restore
    targets — an archived-ignored job should restore to ignored, not further
    back. Retired statuses are mapped to their Phase 6 equivalent.

    Args:
        history: List of to_status strings, newest-first.

    Returns:
        The first valid restorable status, or None if none qualify.
    """
    for status in history:
        mapped = _RETIRED_TO_LIVE.get(status, status)
        if mapped in _RESTORE_SKIP:
            continue
        if mapped in STATUS_VALUES:
            return mapped
    return None


def validate_transition(
    from_status: str,
    to_status: str,
    *,
    force: bool = False,
    i_mean_it: bool = False,
) -> str | None:
    """Validate a status transition.

    Args:
        from_status: Current status.
        to_status: Desired target status.
        force: Bypass the transition graph.
        i_mean_it: Required alongside force for archived → new.

    Returns:
        None if the transition is valid, or an error message string.
    """
    if from_status not in STATUS_VALUES:
        return f"unknown status: {from_status}"
    if to_status not in STATUS_VALUES:
        return f"unknown status: {to_status}"

    allowed = ALLOWED_TRANSITIONS.get(from_status, frozenset())
    if to_status in allowed:
        return None

    if not force:
        return (
            f"transition {from_status} → {to_status} is not allowed; "
            f"valid targets: {sorted(allowed)}"
        )

    if from_status == "archived" and to_status == "new" and not i_mean_it:
        return (
            "archived → new is destructive and requires both "
            "force=True and i_mean_it=True"
        )

    return None


__all__ = [
    "ACTIVE_APPLICATION_STATUSES",
    "ALLOWED_TRANSITIONS",
    "DECAY_SOURCES",
    "DEFAULT_ARCHIVE_IGNORED_DAYS",
    "DEFAULT_FOLLOWUP_GRACE_DAYS",
    "DEFAULT_GHOST_DAYS",
    "LIST_DEFAULT_STATUSES",
    "REASON_AUTO_SCORED",
    "REASON_BULK_CASCADE",
    "REASON_BULK_SELECTED",
    "RESPONSE_STATUSES",
    "STATUS_VALUES",
    "is_terminal",
    "pick_restore_target",
    "validate_transition",
]
