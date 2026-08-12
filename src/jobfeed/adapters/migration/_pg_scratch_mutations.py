"""Real scan/evaluate write benchmarks restricted to an attested scratch DB."""

from __future__ import annotations

import time
from collections.abc import Awaitable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from jobfeed.adapters.migration._pg_benchmark_runner import StoreBenchmarkResult
from jobfeed.domain.models import JobPosting, QualityBand, SaveJobResult, StageAResult

_PLATFORM = "jobfeed-benchmark"
_STAGE_A_SCORE = 73
_MINIMUM_SAMPLES = 30


class _ScratchStore(Protocol):
    async def save_job(self, job: JobPosting) -> SaveJobResult: ...

    async def get_job(self, job_id: str) -> JobPosting | None: ...

    async def claim_stage_a_by_ids(
        self,
        job_ids: list[str],
        *,
        quality_bands: frozenset[str] | None = None,
        corpus: str = "unrated",
        max_days: int | None = None,
        limit: int = 100,
    ) -> list[JobPosting]: ...

    async def release_stage_a_claim(self, job_id: str) -> None: ...

    async def save_stage_a(self, job_id: str, result: StageAResult) -> None: ...

    async def save_stage_a_error(self, job_id: str, error: str) -> None: ...


@dataclass(frozen=True, kw_only=True)
class ScratchMutationConfig:
    """Controls for an isolated scratch mutation benchmark."""

    fixture_prefix: str
    warmup_count: int
    sample_count: int


@dataclass(frozen=True, kw_only=True)
class ScratchMutationTarget:
    """Attested scratch identity required before any write connection opens."""

    dsn: str
    expected_database_identity: str
    source_database_identity: str


@dataclass(frozen=True, kw_only=True)
class ExpectedStageAState:
    """Raw evaluation state expected after one timed production write path."""

    status: str | None
    error: str | None
    score: int | None


@dataclass(frozen=True, kw_only=True)
class ScratchMutationResult:
    """Timings plus raw identities needed for untimed correctness checks."""

    scan_result: StoreBenchmarkResult
    evaluate_result: StoreBenchmarkResult
    path_samples_ms: dict[str, list[float]]
    scan_canonical_ids: tuple[str, ...]
    expected_evaluation_states: dict[str, ExpectedStageAState]


def assert_disposable_scratch_identity(
    *, live_identity: str, expected_identity: str, source_identity: str
) -> None:
    """Reject source or incorrectly attested databases before writes.

    Args:
        live_identity: Identity freshly queried from the target DB.
        expected_identity: Scratch identity from restore evidence.
        source_identity: Immutable source rehearsal identity.

    Raises:
        ValueError: If the target could be source/formal or is not attested scratch.
    """
    if source_identity in {expected_identity, live_identity}:
        raise ValueError("scratch mutation target must be distinct from source")
    if live_identity != expected_identity:
        raise ValueError("live scratch identity differs from attested identity")


def _job(canonical_id: str, *, quality: QualityBand) -> JobPosting:
    now = datetime.now(UTC)
    return JobPosting(
        platform=_PLATFORM,
        canonical_id=canonical_id,
        url=f"https://benchmark.invalid/{canonical_id}",
        title="Scratch benchmark engineer",
        company="Jobfeed benchmark",
        location="Remote",
        discovered_at=now,
        jd_text=(
            "Full benchmark job description with verified responsibilities."
            if quality is QualityBand.FULL
            else "Partial benchmark JD"
        ),
        jd_quality=quality,
        enriched_at=now,
        enrich_source="migration-benchmark",
    )


def _stage_a_result() -> StageAResult:
    return StageAResult(
        score=_STAGE_A_SCORE,
        one_line="Scratch benchmark result",
        timing_eligible="eligible",
        model="migration-benchmark/no-llm",
        prompt_hash="scratch-prompt",
        resume_hash="scratch-resume",
        cost_usd=0.0,
    )


async def _scan_iteration(store: _ScratchStore, canonical_id: str) -> tuple[float, str]:
    started = time.perf_counter_ns()
    inserted = await store.save_job(_job(canonical_id, quality=QualityBand.PARTIAL))
    upgraded = await store.save_job(_job(canonical_id, quality=QualityBand.FULL))
    elapsed_ms = (time.perf_counter_ns() - started) / 1_000_000
    if not inserted.inserted or upgraded.inserted or not upgraded.updated:
        raise ValueError("scan scratch path did not insert then update")
    if inserted.job_id != upgraded.job_id:
        raise ValueError("scan scratch upsert changed the natural-key row identity")
    stored = await store.get_job(inserted.job_id)
    if stored is None or stored.jd_quality is not QualityBand.FULL:
        raise ValueError("scan scratch quality upgrade was not persisted")
    return elapsed_ms, inserted.job_id


async def _claim_one(store: _ScratchStore, job_id: str) -> None:
    claimed = await store.claim_stage_a_by_ids([job_id], limit=1)
    if len(claimed) != 1 or claimed[0].id != job_id:
        raise ValueError("evaluate scratch fixture was not exclusively claimed")


async def _timed_path(operation: Awaitable[None]) -> float:
    started = time.perf_counter_ns()
    await operation
    return (time.perf_counter_ns() - started) / 1_000_000


async def _claim_release(store: _ScratchStore, job_id: str) -> None:
    await _claim_one(store, job_id)
    await store.release_stage_a_claim(job_id)


async def _claim_result(store: _ScratchStore, job_id: str) -> None:
    await _claim_one(store, job_id)
    await store.save_stage_a(job_id, _stage_a_result())


async def _claim_error(store: _ScratchStore, job_id: str) -> None:
    await _claim_one(store, job_id)
    await store.save_stage_a_error(job_id, "migration-benchmark-error")


async def _create_evaluate_fixtures(
    store: _ScratchStore, prefix: str, count: int
) -> list[tuple[str, str, str]]:
    """Create three fixtures per sample.

    Time complexity is O(count * fixed-path-count).
    """
    fixtures = []
    for index in range(count):
        ids = []
        for path in ("release", "result", "error"):
            saved = await store.save_job(
                _job(f"{prefix}-evaluate-{path}-{index}", quality=QualityBand.FULL)
            )
            if not saved.inserted:
                raise ValueError("evaluate scratch fixture already exists")
            ids.append(saved.job_id)
        fixtures.append((ids[0], ids[1], ids[2]))
    return fixtures


def _validate_config(config: ScratchMutationConfig) -> int:
    if not config.fixture_prefix:
        raise ValueError("scratch fixture prefix must be non-empty")
    if config.warmup_count < 1 or config.sample_count < _MINIMUM_SAMPLES:
        raise ValueError("scratch mutation benchmark requires warmup and 30 samples")
    return config.warmup_count + config.sample_count


async def run_scratch_mutation_workloads(
    store: _ScratchStore, config: ScratchMutationConfig
) -> ScratchMutationResult:
    """Run real production scan/evaluate writes on an already-approved store.

    Setup writes and correctness reads are outside timed intervals. Time
    complexity is O(warmups + samples), and retained evidence is O(samples).

    Args:
        store: Connected production-compatible PostgreSQL store.
        config: Fixture namespace and sample controls.

    Returns:
        Timings and expected persistence state for raw post-run verification.

    Raises:
        ValueError: If any production path or correctness assertion differs.
    """
    total = _validate_config(config)
    fixtures = await _create_evaluate_fixtures(store, config.fixture_prefix, total)
    scan_samples: list[float] = []
    evaluate_samples: list[float] = []
    path_samples: dict[str, list[float]] = {
        "claim_release": [],
        "claim_result": [],
        "claim_error": [],
    }
    scan_ids = []
    expected: dict[str, ExpectedStageAState] = {}
    for index, (release_id, result_id, error_id) in enumerate(fixtures):
        scan_ms, _ = await _scan_iteration(
            store, f"{config.fixture_prefix}-scan-{index}"
        )
        release_ms = await _timed_path(_claim_release(store, release_id))
        result_ms = await _timed_path(_claim_result(store, result_id))
        error_ms = await _timed_path(_claim_error(store, error_id))
        scan_ids.append(f"{config.fixture_prefix}-scan-{index}")
        expected[release_id] = ExpectedStageAState(status=None, error=None, score=None)
        expected[result_id] = ExpectedStageAState(
            status="completed", error=None, score=_STAGE_A_SCORE
        )
        expected[error_id] = ExpectedStageAState(
            status="error", error="migration-benchmark-error", score=None
        )
        if index >= config.warmup_count:
            scan_samples.append(scan_ms)
            evaluate_samples.append(release_ms + result_ms + error_ms)
            path_samples["claim_release"].append(release_ms)
            path_samples["claim_result"].append(result_ms)
            path_samples["claim_error"].append(error_ms)
    return ScratchMutationResult(
        scan_result=StoreBenchmarkResult(samples_ms=scan_samples, row_count=total),
        evaluate_result=StoreBenchmarkResult(
            samples_ms=evaluate_samples, row_count=total * 3
        ),
        path_samples_ms=path_samples,
        scan_canonical_ids=tuple(scan_ids),
        expected_evaluation_states=expected,
    )
