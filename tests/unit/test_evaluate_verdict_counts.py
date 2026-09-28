"""Evaluation recommendations are counted for their own run."""

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast

from jobfeed.domain.models import PipelineRun, StageBResult, Verdict
from jobfeed.services._evaluate_stage_b import _record_verdict


def test_each_stage_b_recommendation_increments_its_run_bucket() -> None:
    run = PipelineRun(
        run_id="run-1",
        started_at=datetime(2026, 9, 23, tzinfo=UTC),
        source="evaluate",
        verdict_counts={"apply": 0, "consider": 0, "skip": 0},
    )
    for verdict in (Verdict.APPLY, Verdict.CONSIDER, Verdict.SKIP, Verdict.APPLY):
        _record_verdict(
            run,
            cast(StageBResult, SimpleNamespace(verdict=verdict)),
        )
    assert run.verdict_counts == {"apply": 2, "consider": 1, "skip": 1}
