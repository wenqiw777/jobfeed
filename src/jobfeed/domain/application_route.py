"""Observed application navigation evidence, separate from merge authority."""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal
from urllib.parse import urldefrag

RouteStatus = Literal["resolved", "unresolved", "ambiguous", "blocked", "failed"]
HopKind = Literal[
    "original_apply_url",
    "http_redirect",
    "target_apply_link",
    "target_embedded_job_url",
    "rendered_page",
]
_MIN_LOOP_HOPS = 2


def is_in_page_application_loop(evidence: object) -> bool:
    """Recognize old false loops ending at a same-document Apply anchor.

    Args:
        evidence: Existing saved route outcome, possibly malformed.

    Returns:
        Whether a corrected resolver should retry this specific failure.
    """
    if (
        not isinstance(evidence, dict)
        or evidence.get("status") != "unresolved"
        or evidence.get("reason") != "navigation_loop"
    ):
        return False
    hops = evidence.get("hops")
    if not isinstance(hops, list) or len(hops) < _MIN_LOOP_HOPS:
        return False
    previous, last = hops[-2:]
    if not isinstance(previous, dict) or not isinstance(last, dict):
        return False
    if last.get("kind") != "target_apply_link":
        return False
    source, target = previous.get("url"), last.get("url")
    if not isinstance(source, str) or not isinstance(target, str):
        return False
    try:
        document, fragment = urldefrag(target)
        return bool(fragment and document == urldefrag(source)[0])
    except ValueError:
        return False


@dataclass(frozen=True)
class ApplicationRouteHop:
    """One actually observed navigation edge."""

    url: str
    kind: HopKind


@dataclass(frozen=True)
class ApplicationRouteOutcome:
    """Only resolved ATS URLs may contribute canonical identity evidence."""

    status: RouteStatus
    ats_url: str | None = None
    hops: tuple[ApplicationRouteHop, ...] = ()
    reason: str = ""
    checked_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True)
class RenderedApplicationPage:
    """Read-only Chrome snapshot of the final document and serialized DOM."""

    url: str
    html: str


class ApplicationPageReadError(Exception):
    """Chrome observer reports a contained availability or snapshot failure."""

    def __init__(self, status: Literal["blocked", "failed"], reason: str) -> None:
        self.status, self.reason = status, reason
        super().__init__(reason)
