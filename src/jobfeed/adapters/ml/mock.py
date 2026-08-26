"""Deterministic mock ML-gate adapter for fast unit/CI use.

``MockGate`` runs the real numpy-free feature extraction and hard-fail rules
from ``domain.ml_features`` so a clearly non-SWE posting always fails with the
correct reason, regardless of the configured ``default_result``. Experience and
clearance remain feature metadata. Only the *model* verdict is mocked: a
posting that clears the clear-non-SDE rule yields ``default_result`` (or the
optional ``fail_if`` predicate's verdict) with fixed pass/fail scores. It mirrors the
``MockLLM`` adapter style (a first-class deterministic test double behind a
port), but is predict-only and never makes network or subprocess calls.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from jobfeed.domain.ml_features import (
    MLGateFeatures,
    extract_features,
    hard_fail_reason,
)
from jobfeed.domain.models import MLGateResult
from jobfeed.ports.ml_gate import GateInput

PASS_SCORE = 1.0
FAIL_SCORE = 0.0


class MockGate:
    """MLGate implementation with deterministic, configurable verdicts."""

    def __init__(
        self,
        *,
        default_result: str = "pass",
        fail_if: Callable[[MLGateFeatures], bool] | None = None,
    ) -> None:
        """Configure the mock gate's non-hard-fail verdict.

        Args:
            default_result: Verdict ("pass"/"fail") for jobs clearing all
                hard-fail rules and not matched by ``fail_if``.
            fail_if: Optional predicate over extracted features; when it
                returns True for a non-hard-failing job, the verdict is a
                model-style "fail" (``fail_reason=None``).
        """
        self._default_result = default_result
        self._fail_if = fail_if

    async def predict_batch(self, jobs: list[GateInput]) -> list[MLGateResult]:
        """Score a batch of jobs, one ordered result per input.

        Like XGBoostGate, the regex-heavy feature extraction runs in a
        worker thread so a large batch (the dev/test ``model_dir="mock"``
        escape hatch is reachable from the live server) cannot block the
        caller's event loop.

        Args:
            jobs: Gate inputs to score; ``result[i]`` corresponds to ``jobs[i]``.

        Returns:
            One ``MLGateResult`` per input, in the same order as ``jobs``.
        """
        if not jobs:
            return []
        return await asyncio.to_thread(self._predict_batch_sync, jobs)

    def _predict_batch_sync(self, jobs: list[GateInput]) -> list[MLGateResult]:
        return [self._predict_one(job) for job in jobs]

    def _predict_one(self, job: GateInput) -> MLGateResult:
        features = extract_features(job.title, job.jd_text)
        reason = hard_fail_reason(features)
        if reason is not None:
            return _result(features, result="fail", reason=reason, score=FAIL_SCORE)
        if self._fail_if is not None and self._fail_if(features):
            return _result(features, result="fail", reason=None, score=FAIL_SCORE)
        score = PASS_SCORE if self._default_result == "pass" else FAIL_SCORE
        return _result(features, result=self._default_result, reason=None, score=score)


def _result(
    features: MLGateFeatures,
    *,
    result: str,
    reason: str | None,
    score: float,
) -> MLGateResult:
    """Build an MLGateResult from features, coercing int columns to bool.

    Args:
        features: Extracted structured features for the posting.
        result: Verdict string ("pass" or "fail").
        reason: Hard-fail reason, or None for a model-style verdict.
        score: Fixed gate score to record.

    Returns:
        A populated ``MLGateResult`` with ``version="mock"``.
    """
    return MLGateResult(
        score=score,
        result=result,
        fail_reason=reason,
        version="mock",
        is_swe_role=features.is_swe_role,
        seniority_level=features.seniority_level,
        degree_required=features.degree_required,
        clearance_required=bool(features.clearance_required),
        school_restricted=bool(features.school_restricted),
        yoe_min=features.yoe_min,
        domain_tags=features.domain_tags,
        tech_required=features.tech_required,
        role_type=features.role_type,
    )


__all__ = ["MockGate"]
