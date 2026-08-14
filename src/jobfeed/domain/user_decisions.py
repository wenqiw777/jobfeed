"""User-facing decisions derived from internal workflow states."""

from __future__ import annotations

from typing import Literal, TypeAlias

UserDecision: TypeAlias = Literal["results", "wait", "applied", "ignored"]

_DECISION_STATUSES: dict[UserDecision, tuple[str, ...]] = {
    "results": ("new", "scored"),
    "wait": ("shortlisted", "awaiting_referral"),
    "applied": ("applied", "interviewing", "offer", "rejected", "ghosted"),
    "ignored": ("ignored", "archived"),
}
_STATUS_DECISIONS = {
    status: decision
    for decision, statuses in _DECISION_STATUSES.items()
    for status in statuses
}


def decision_for_status(status: str) -> UserDecision | None:
    """Return the user decision represented by a workflow status.

    Args:
        status: Persisted workflow status.

    Returns:
        User-facing decision, or ``None`` for an unknown state.
    """
    return _STATUS_DECISIONS.get(status)


def statuses_for_decision(decision: UserDecision) -> tuple[str, ...]:
    """Return the exact workflow states represented by a decision filter.

    Args:
        decision: Public four-way decision value.

    Returns:
        Non-overlapping workflow statuses for the requested decision.
    """
    return _DECISION_STATUSES[decision]


__all__ = ["UserDecision", "decision_for_status", "statuses_for_decision"]
