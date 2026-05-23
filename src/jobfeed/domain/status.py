"""Pure domain logic for job status transitions."""

from __future__ import annotations

STATUS_VALUES: frozenset[str] = frozenset(
    {
        "new",
        "scored",
        "shortlisted",
        "applied",
        "oa",
        "hr_call",
        "second_round",
        "final_round",
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
    "scored": frozenset({"shortlisted", "applied", "archived", "ignored"}),
    "shortlisted": frozenset({"applied", "archived"}),
    "applied": frozenset(
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
    ),
    "oa": frozenset(
        {
            "hr_call",
            "second_round",
            "final_round",
            "offer",
            "rejected",
            "ghosted",
        }
    ),
    "hr_call": frozenset(
        {
            "second_round",
            "final_round",
            "offer",
            "rejected",
            "ghosted",
        }
    ),
    "second_round": frozenset({"final_round", "offer", "rejected", "ghosted"}),
    "final_round": frozenset({"offer", "rejected", "ghosted"}),
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

DECAY_SOURCES: frozenset[str] = frozenset(
    {
        "applied",
        "interviewing",
        "oa",
        "hr_call",
        "second_round",
        "final_round",
    }
)

# Statuses where an application is still active (post-submit, pre-terminal).
# Identical to DECAY_SOURCES: an application is "active" exactly while it remains
# subject to ghost/auto-decay. Used for the same-company reapply guard.
ACTIVE_APPLICATION_STATUSES: frozenset[str] = DECAY_SOURCES

RESPONSE_STATUSES: frozenset[str] = frozenset(
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


def is_terminal(status: str) -> bool:
    """Check whether a status has no allowed outgoing transitions.

    Args:
        status: Status string to check.

    Returns:
        True if the status is terminal.
    """
    return status in _TERMINAL


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
    "ALLOWED_TRANSITIONS",
    "DECAY_SOURCES",
    "RESPONSE_STATUSES",
    "STATUS_VALUES",
    "is_terminal",
    "validate_transition",
]
