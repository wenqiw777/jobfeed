"""Unit tests for the deterministic MockGate ML-gate adapter."""

from __future__ import annotations

import dataclasses

import jobfeed.ports.ml_gate as ml_gate_module
from jobfeed.adapters.ml.mock import MockGate
from jobfeed.domain.ml_features import extract_features
from jobfeed.ports.ml_gate import GateInput, MLGate

# Clean entry-level SWE posting: passes every hard-fail rule.
CLEAN_TITLE = "Software Engineer"
CLEAN_JD = "Entry-level role. Write code in Python. Join us."

# A senior posting whose JD demands 5+ YoE -> hard fail regardless of config.
SENIOR_TITLE = "Software Engineer"
SENIOR_JD = "We require 5+ years of experience writing code."

# A non-SWE posting -> hard fail regardless of config.
NON_SWE_TITLE = "Sales Manager"
NON_SWE_JD = "Sell products to clients."

# An active-clearance posting -> hard fail regardless of config.
CLEARANCE_TITLE = "Software Engineer"
CLEARANCE_JD = "Must hold an active TS/SCI clearance. Write code."

PASS_SCORE = 1.0
FAIL_SCORE = 0.0
SENIOR_YOE_MIN = 5


def _clean_input() -> GateInput:
    return GateInput(job_id="clean-1", title=CLEAN_TITLE, jd_text=CLEAN_JD)


def test_mock_gate_satisfies_ml_gate_protocol() -> None:
    """MockGate should structurally implement the MLGate port."""
    assert isinstance(MockGate(), MLGate)


def test_ml_gate_is_runtime_checkable_and_domain_only() -> None:
    """The MLGate port stays predict-only without a ModelNotFoundError."""
    assert hasattr(MLGate, "predict_batch")
    assert not hasattr(ml_gate_module, "ModelNotFoundError")


def test_gate_input_is_frozen_dataclass() -> None:
    """GateInput should be an immutable record of job_id/title/jd_text."""
    gate_input = _clean_input()
    assert dataclasses.is_dataclass(gate_input)
    assert (gate_input.job_id, gate_input.title) == ("clean-1", CLEAN_TITLE)
    try:
        gate_input.job_id = "other"  # type: ignore[misc]
    except dataclasses.FrozenInstanceError:
        return
    raise AssertionError("GateInput must be frozen")


async def test_predict_batch_empty_input_returns_empty() -> None:
    """An empty batch should produce an empty, order-preserving result."""
    results = await MockGate().predict_batch([])
    assert results == []


async def test_predict_batch_is_ordered_one_per_input() -> None:
    """Results must be one-per-input and aligned to the input order."""
    jobs = [
        GateInput(job_id="a", title=CLEAN_TITLE, jd_text=CLEAN_JD),
        GateInput(job_id="b", title=NON_SWE_TITLE, jd_text=NON_SWE_JD),
        GateInput(job_id="c", title=CLEAN_TITLE, jd_text=CLEAN_JD),
    ]
    results = await MockGate().predict_batch(jobs)

    assert len(results) == len(jobs)
    assert [r.result for r in results] == ["pass", "fail", "pass"]


async def test_default_pass_for_clean_swe_role() -> None:
    """A clean entry-level SWE posting passes with the fixed pass score."""
    results = await MockGate().predict_batch([_clean_input()])

    result = results[0]
    assert result.result == "pass"
    assert result.fail_reason is None
    assert result.score == PASS_SCORE
    assert result.version == "mock"


async def test_default_result_override_to_fail_is_honored() -> None:
    """default_result='fail' yields a model-style fail (no hard reason)."""
    results = await MockGate(default_result="fail").predict_batch([_clean_input()])

    result = results[0]
    assert result.result == "fail"
    assert result.fail_reason is None
    assert result.score == FAIL_SCORE


async def test_hard_fail_yoe_overrides_default_pass() -> None:
    """A yoe>=2 posting always hard-fails with the exact reason."""
    results = await MockGate(default_result="pass").predict_batch(
        [GateInput(job_id="s", title=SENIOR_TITLE, jd_text=SENIOR_JD)],
    )

    result = results[0]
    assert result.result == "fail"
    assert result.fail_reason == "yoe_min >= 5"
    assert result.score == FAIL_SCORE
    assert result.yoe_min == SENIOR_YOE_MIN


async def test_hard_fail_non_swe_overrides_default_pass() -> None:
    """A non-SWE posting always hard-fails regardless of default_result."""
    results = await MockGate(default_result="pass").predict_batch(
        [GateInput(job_id="n", title=NON_SWE_TITLE, jd_text=NON_SWE_JD)],
    )

    result = results[0]
    assert result.result == "fail"
    assert result.fail_reason == "not software engineering role"
    assert result.is_swe_role is False


async def test_hard_fail_active_clearance_overrides_default_pass() -> None:
    """An active-clearance posting always hard-fails regardless of config."""
    results = await MockGate(default_result="pass").predict_batch(
        [GateInput(job_id="cl", title=CLEARANCE_TITLE, jd_text=CLEARANCE_JD)],
    )

    result = results[0]
    assert result.result == "fail"
    assert result.fail_reason == "active clearance required"


async def test_fail_if_predicate_is_honored_when_no_hard_fail() -> None:
    """The fail_if predicate decides the verdict for non-hard-failing jobs."""
    gate = MockGate(fail_if=lambda features: "python" in features.tech_required)
    results = await gate.predict_batch([_clean_input()])

    result = results[0]
    assert result.result == "fail"
    assert result.fail_reason is None
    assert result.score == FAIL_SCORE


async def test_clearance_and_school_columns_are_coerced_to_bool() -> None:
    """int 0/1 feature columns surface as bool on the MLGateResult."""
    results = await MockGate().predict_batch([_clean_input()])

    result = results[0]
    assert result.clearance_required is False
    assert result.school_restricted is False
    assert isinstance(result.clearance_required, bool)
    assert isinstance(result.school_restricted, bool)


async def test_feature_columns_are_populated_from_extraction() -> None:
    """MockGate copies the extracted feature columns onto the result."""
    features = extract_features(CLEAN_TITLE, CLEAN_JD)
    results = await MockGate().predict_batch([_clean_input()])

    result = results[0]
    assert result.seniority_level == features.seniority_level
    assert result.tech_required == features.tech_required == ["python"]
    assert result.role_type == features.role_type
    assert result.domain_tags == features.domain_tags
