"""PostgreSQL production-store benchmark seed tests."""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from jobfeed.adapters.migration._baseline_workload import BenchmarkQuery
from jobfeed.adapters.migration._pg_benchmark_runner import (
    _require_nonempty_result,
    _seed_inputs,
)

_SEED_LIMIT = 100


@dataclass(frozen=True)
class _Job:
    id: int


@dataclass(frozen=True)
class _Row:
    job: _Job
    company_norm: str
    title_norm: str


class _Store:
    def __init__(self) -> None:
        self.checked_ids: list[str] = []

    async def list_jobs(self, *, limit: int) -> list[_Job]:
        """Return a non-empty bounded seed list."""
        assert limit == _SEED_LIMIT
        return [_Job(1), _Job(2)]

    async def query_jobs_view(self, query: object) -> object:
        """Return real view-shaped candidates in display order."""
        del query
        return SimpleNamespace(
            rows=[
                _Row(_Job(1), "solo", "engineer"),
                _Row(_Job(2), "twin", "engineer"),
            ]
        )

    async def list_twin_statuses(self, job_id: str) -> list[object]:
        """Only the second candidate has persisted twin rows."""
        self.checked_ids.append(job_id)
        return [object()] if job_id == "2" else []


@pytest.mark.asyncio
async def test_seed_inputs_selects_a_job_proven_to_have_persisted_twins() -> None:
    """The timed twin-status path never uses an arbitrary singleton job."""
    store = _Store()

    seeds = await _seed_inputs(store, limit=_SEED_LIMIT)  # type: ignore[arg-type]

    assert seeds.job_id == "2"
    assert store.checked_ids == ["1", "2"]


def test_only_explicit_empty_aggregate_accepts_zero_rows() -> None:
    """Empty step metrics are valid without weakening hot-path gates."""
    step_timings = BenchmarkQuery(
        name="step_timings",
        coverage="perf.step_timings",
        operation="get_step_timings",
        params={"window_days": 30},
        allow_empty=True,
    )
    _require_nonempty_result(step_timings, 0)

    hot_path = BenchmarkQuery(
        name="jobs_view",
        coverage="hot.list",
        operation="jobs_view_list",
        params={"limit": _SEED_LIMIT},
        allow_empty=False,
    )
    with pytest.raises(ValueError, match="returned zero rows"):
        _require_nonempty_result(hot_path, 0)
