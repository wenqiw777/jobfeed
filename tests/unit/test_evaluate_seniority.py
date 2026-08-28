"""Pipeline-boundary tests for the independent seniority gate."""

from datetime import UTC, datetime

import pytest

from jobfeed.domain.models import JobPosting
from jobfeed.domain.seniority import SeniorityDecision, SeniorityInput
from jobfeed.services._evaluate_seniority import apply_seniority_gate


class _FixedGate:
    async def predict_batch(
        self, jobs: list[SeniorityInput]
    ) -> list[SeniorityDecision]:
        return [
            SeniorityDecision(
                result="out_of_scope" if job.job_id in {"11", "20"} else "in_scope",
                reason="test",
                yoe_min=3 if job.job_id in {"11", "20"} else 2,
                confidence=1.0,
            )
            for job in jobs
        ]


def _job(job_id: str) -> JobPosting:
    return JobPosting(
        id=job_id,
        platform="test",
        canonical_id=job_id,
        url=f"https://example.com/{job_id}",
        title="Software Engineer",
        company="Example",
        location="United States",
        discovered_at=datetime.now(UTC),
        jd_text="Build software.",
    )


@pytest.mark.asyncio
async def test_filter_blocks_out_scope_but_explores_ten_percent() -> None:
    jobs = [_job("11"), _job("20"), _job("21")]

    survivors, blocked = await apply_seniority_gate(_FixedGate(), jobs, mode="filter")

    assert [job.id for job in survivors] == ["20", "21"]
    assert blocked == 1


@pytest.mark.asyncio
async def test_shadow_scores_but_never_blocks() -> None:
    jobs = [_job("11"), _job("20")]

    survivors, blocked = await apply_seniority_gate(_FixedGate(), jobs, mode="shadow")

    assert survivors == jobs
    assert blocked == 0
