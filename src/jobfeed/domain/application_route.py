"""Observed application navigation evidence, separate from merge authority."""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

RouteStatus = Literal["resolved", "unresolved", "ambiguous", "blocked", "failed"]
HopKind = Literal[
    "original_apply_url",
    "http_redirect",
    "target_apply_link",
    "target_embedded_job_url",
    "rendered_page",
]


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
