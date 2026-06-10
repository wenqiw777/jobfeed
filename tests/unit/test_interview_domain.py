"""Unit tests for the interview domain model and constants."""

from __future__ import annotations

from datetime import datetime

from jobfeed.domain.interview import (
    PRESET_INTERVIEW_LABELS,
    RETIRED_STATUS_LABELS,
    InterviewRound,
)

_SAMPLE_JOB_ID = 42


def test_interview_round_fields() -> None:
    """InterviewRound should accept all declared fields."""
    now = datetime(2026, 6, 9, 12, 0, 0)
    r = InterviewRound(
        id=1,
        job_id=_SAMPLE_JOB_ID,
        round_index=0,
        label="Phone Screen",
        scheduled_at=now,
        completed_at=now,
        notes="went well",
        created_at=now,
    )
    assert r.id == 1
    assert r.job_id == _SAMPLE_JOB_ID
    assert r.round_index == 0
    assert r.label == "Phone Screen"
    assert r.scheduled_at == now
    assert r.completed_at == now
    assert r.notes == "went well"
    assert r.created_at == now


def test_interview_round_defaults() -> None:
    """Optional fields should default to None."""
    r = InterviewRound(job_id=1, round_index=0, label="Technical")
    assert r.id is None
    assert r.scheduled_at is None
    assert r.completed_at is None
    assert r.notes is None
    assert r.created_at is None


def test_preset_interview_labels_non_empty() -> None:
    """PRESET_INTERVIEW_LABELS should be a non-empty list of strings."""
    assert isinstance(PRESET_INTERVIEW_LABELS, list)
    assert len(PRESET_INTERVIEW_LABELS) > 0
    for label in PRESET_INTERVIEW_LABELS:
        assert isinstance(label, str)


def test_retired_status_labels_maps_all_four() -> None:
    """RETIRED_STATUS_LABELS should map all four retired interview statuses."""
    expected_keys = {"oa", "hr_call", "second_round", "final_round"}
    assert set(RETIRED_STATUS_LABELS.keys()) == expected_keys


def test_retired_status_labels_values() -> None:
    """RETIRED_STATUS_LABELS should have human-readable display names."""
    assert RETIRED_STATUS_LABELS["oa"] == "OA"
    assert RETIRED_STATUS_LABELS["hr_call"] == "HR Call"
    assert RETIRED_STATUS_LABELS["second_round"] == "2nd Round"
    assert RETIRED_STATUS_LABELS["final_round"] == "Final Round"
