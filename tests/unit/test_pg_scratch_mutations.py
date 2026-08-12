"""Contracts for real scratch-only PostgreSQL mutation benchmarks."""

from __future__ import annotations

from dataclasses import replace

import pytest

from jobfeed.adapters.migration._pg_scratch_mutations import (
    ScratchMutationConfig,
    assert_disposable_scratch_identity,
    run_scratch_mutation_workloads,
)
from jobfeed.domain.models import JobEvaluation, JobPosting, SaveJobResult, StageAResult

_SAMPLES = 30
_WARMUPS = 1


class _FakeStore:
    def __init__(self) -> None:
        self.jobs_by_key: dict[tuple[str, str], JobPosting] = {}
        self.ids_by_key: dict[tuple[str, str], str] = {}
        self.states: dict[str, str | None] = {}
        self.calls: list[str] = []

    async def save_job(self, job: JobPosting) -> SaveJobResult:
        self.calls.append("save_job")
        key = (job.platform, job.canonical_id)
        job_id = self.ids_by_key.setdefault(key, str(len(self.ids_by_key) + 1))
        inserted = key not in self.jobs_by_key
        self.jobs_by_key[key] = replace(job, id=job_id)
        return SaveJobResult(job_id=job_id, inserted=inserted, updated=not inserted)

    async def get_job(self, job_id: str) -> JobPosting | None:
        return next(
            (job for job in self.jobs_by_key.values() if job.id == job_id), None
        )

    async def claim_stage_a_by_ids(
        self, job_ids: list[str], **_kwargs: object
    ) -> list[JobPosting]:
        self.calls.append("claim_stage_a_by_ids")
        job = await self.get_job(job_ids[0])
        if job is None or self.states.get(job_ids[0]) not in (None, "error"):
            return []
        self.states[job_ids[0]] = "in_progress"
        return [job]

    async def release_stage_a_claim(self, job_id: str) -> None:
        self.calls.append("release_stage_a_claim")
        self.states[job_id] = None

    async def save_stage_a(self, job_id: str, _result: StageAResult) -> None:
        self.calls.append("save_stage_a")
        self.states[job_id] = "completed"

    async def save_stage_a_error(self, job_id: str, _error: str) -> None:
        self.calls.append("save_stage_a_error")
        self.states[job_id] = "error"

    async def get_evaluation(self, _job_id: str) -> JobEvaluation | None:
        raise AssertionError("raw verification belongs outside the timed path")


@pytest.mark.asyncio
async def test_real_scratch_mutations_cover_scan_and_evaluate_write_paths() -> None:
    """Setup is untimed; every required production write path has 30 samples."""
    store = _FakeStore()

    result = await run_scratch_mutation_workloads(
        store,
        ScratchMutationConfig(
            fixture_prefix="jobfeed-benchmark-test",
            warmup_count=_WARMUPS,
            sample_count=_SAMPLES,
        ),
    )

    assert len(result.scan_result.samples_ms) == _SAMPLES
    assert len(result.evaluate_result.samples_ms) == _SAMPLES
    assert {name: len(values) for name, values in result.path_samples_ms.items()} == {
        "claim_release": _SAMPLES,
        "claim_result": _SAMPLES,
        "claim_error": _SAMPLES,
    }
    total = _SAMPLES + _WARMUPS
    assert store.calls.count("save_job") == total * 5
    assert store.calls.count("claim_stage_a_by_ids") == total * 3
    assert store.calls.count("release_stage_a_claim") == total
    assert store.calls.count("save_stage_a") == total
    assert store.calls.count("save_stage_a_error") == total
    assert len(store.jobs_by_key) == total * 4
    assert {state.status for state in result.expected_evaluation_states.values()} == {
        None,
        "completed",
        "error",
    }


def test_scratch_identity_must_be_live_expected_and_distinct_from_source() -> None:
    """A source/formal identity is rejected before any mutation is authorized."""
    assert_disposable_scratch_identity(
        live_identity="scratch", expected_identity="scratch", source_identity="source"
    )

    with pytest.raises(ValueError, match="source"):
        assert_disposable_scratch_identity(
            live_identity="source",
            expected_identity="source",
            source_identity="source",
        )
    with pytest.raises(ValueError, match="attested"):
        assert_disposable_scratch_identity(
            live_identity="other",
            expected_identity="scratch",
            source_identity="source",
        )
