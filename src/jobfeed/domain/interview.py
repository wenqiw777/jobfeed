"""Domain model and constants for interview round tracking."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

PRESET_INTERVIEW_LABELS: list[str] = [
    "Phone Screen",
    "Technical",
    "Behavioral",
    "System Design",
    "Hiring Manager",
    "Team Fit",
    "Final",
]

RETIRED_STATUS_LABELS: dict[str, str] = {
    "oa": "OA",
    "hr_call": "HR Call",
    "second_round": "2nd Round",
    "final_round": "Final Round",
}


@dataclass(kw_only=True)
class InterviewRound:
    """A single interview round associated with a job application."""

    id: int | None = None
    job_id: int
    round_index: int
    label: str
    scheduled_at: datetime | None = None
    completed_at: datetime | None = None
    notes: str | None = None
    created_at: datetime | None = None


__all__ = [
    "PRESET_INTERVIEW_LABELS",
    "RETIRED_STATUS_LABELS",
    "InterviewRound",
]
