"""Pipeline-boundary tests for the independent seniority gate."""

from datetime import UTC, datetime
from typing import Literal

import pytest

from jobfeed.domain.models import JobPosting
from jobfeed.domain.seniority import SeniorityDecision, SeniorityInput
from jobfeed.services._evaluate_seniority import apply_seniority_gate
from jobfeed.services.seniority_gate import HybridSeniorityGate


class _FixedGate:
    def __init__(
        self,
        *,
        source: Literal["rule", "model"] = "model",
        confidence: float = 0.94,
        out_of_scope_ids: set[str] | None = None,
    ) -> None:
        self._source = source
        self._confidence = confidence
        self._out_of_scope_ids = (
            {"11", "20"} if out_of_scope_ids is None else out_of_scope_ids
        )

    async def predict_batch(
        self, jobs: list[SeniorityInput]
    ) -> list[SeniorityDecision]:
        return [
            SeniorityDecision(
                result=(
                    "out_of_scope"
                    if job.job_id in self._out_of_scope_ids
                    else "in_scope"
                ),
                reason="test",
                yoe_min=3 if job.job_id in self._out_of_scope_ids else 2,
                confidence=self._confidence,
                source=self._source,
                version="model-v1",
            )
            for job in jobs
        ]


def _job(
    job_id: str,
    *,
    canonical_id: str | None = None,
    title: str = "Software Engineer",
    jd_text: str = "Build software.",
) -> JobPosting:
    return JobPosting(
        id=job_id,
        platform="test",
        canonical_id=canonical_id or job_id,
        url=f"https://example.com/{job_id}",
        title=title,
        company="Example",
        location="United States",
        discovered_at=datetime.now(UTC),
        jd_text=jd_text,
    )


@pytest.mark.asyncio
async def test_filter_explores_five_percent_of_uncertain_model_results() -> None:
    jobs = [_job("job-0"), _job("job-5")]
    gate = _FixedGate(out_of_scope_ids={"job-0", "job-5"})

    survivors, blocked = await apply_seniority_gate(gate, jobs, mode="filter")

    assert [job.id for job in survivors] == ["job-5"]
    assert blocked == 1


@pytest.mark.asyncio
async def test_filter_never_explores_high_confidence_model_result() -> None:
    jobs = [_job("job-5")]
    gate = _FixedGate(confidence=0.95, out_of_scope_ids={"job-5"})

    survivors, blocked = await apply_seniority_gate(gate, jobs, mode="filter")

    assert survivors == []
    assert blocked == 1


@pytest.mark.asyncio
async def test_filter_exploration_is_stable_across_database_ids() -> None:
    jobs = [
        _job("101", canonical_id="job-5"),
        _job("202", canonical_id="job-5"),
    ]
    gate = _FixedGate(out_of_scope_ids={"101", "202"})

    survivors, blocked = await apply_seniority_gate(gate, jobs, mode="filter")

    assert survivors == jobs
    assert blocked == 0


@pytest.mark.asyncio
async def test_filter_caps_model_exploration_at_ten_jobs() -> None:
    expected_explored = 10
    expected_blocked = 2
    selected_ids = {
        f"job-{number}"
        for number in [5, 27, 63, 159, 160, 168, 180, 183, 213, 220, 333, 341]
    }
    jobs = [_job(job_id) for job_id in selected_ids]
    gate = _FixedGate(out_of_scope_ids=selected_ids)

    survivors, blocked = await apply_seniority_gate(gate, jobs, mode="filter")

    assert len(survivors) == expected_explored
    assert blocked == expected_blocked


@pytest.mark.asyncio
async def test_filter_never_explores_rule_out_scope() -> None:
    jobs = [_job("20")]

    survivors, blocked = await apply_seniority_gate(
        _FixedGate(source="rule"), jobs, mode="filter"
    )

    assert survivors == []
    assert blocked == 1


@pytest.mark.asyncio
async def test_filter_blocks_rule_out_scope_for_exploration_bucket_job() -> None:
    jobs = [
        _job(
            "431020",
            title="Sr. AI Engineer",
            jd_text="Minimum 10 years of professional engineering experience required.",
        )
    ]
    gate = HybridSeniorityGate(model=None, out_of_scope_threshold=0.9)

    survivors, blocked = await apply_seniority_gate(gate, jobs, mode="filter")

    assert survivors == []
    assert blocked == 1


@pytest.mark.asyncio
async def test_shadow_scores_but_never_blocks() -> None:
    jobs = [_job("11"), _job("20")]

    survivors, blocked = await apply_seniority_gate(_FixedGate(), jobs, mode="shadow")

    assert survivors == jobs
    assert blocked == 0
