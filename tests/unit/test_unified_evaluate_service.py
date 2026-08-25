"""One-call service contract for the canonical unified evaluator."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from jobfeed.domain.filtering import HardFilters
from jobfeed.domain.models import (
    AutoDecayResult,
    JobPosting,
    LLMResponse,
    QualityBand,
    UnifiedEvaluationResult,
)
from jobfeed.domain.unified_evaluation_parse import (
    parse_unified_evaluation_response,
)
from jobfeed.ports.prompts import PromptBundle
from jobfeed.services.evaluate import EvaluateService
from jobfeed.services.evaluate_types import (
    EvaluateDependencies,
    EvaluateLLMConfig,
    EvaluateRuntimeConfig,
)
from tests.support.run_leases import SuccessfulRunLeaseMixin

_FULL_MATCH = 100


class _Store(SuccessfulRunLeaseMixin):
    def __init__(self, job: JobPosting | list[JobPosting]) -> None:
        self.jobs = job if isinstance(job, list) else [job]
        self.saved: list[tuple[str, UnifiedEvaluationResult]] = []
        self.errors: list[tuple[str, str, str]] = []
        self.claims = 0
        self.previews = 0
        self.claim_job_ids: list[list[str] | None] = []
        self.auto_decay_calls: list[dict[str, object]] = []

    async def claim_pending_evaluations(self, **kwargs: object) -> list[JobPosting]:
        self.claims += 1
        raw_ids = kwargs.get("job_ids")
        job_ids = list(raw_ids) if isinstance(raw_ids, list) else None
        self.claim_job_ids.append(job_ids)
        if job_ids is None:
            return list(self.jobs)
        wanted = set(job_ids)
        return [job for job in self.jobs if job.id in wanted]

    async def preview_pending_evaluations(self, **_kwargs: object) -> list[JobPosting]:
        self.previews += 1
        return list(self.jobs)

    async def save_evaluation(
        self, job_id: str, result: UnifiedEvaluationResult, _claim_token: str
    ) -> None:
        self.saved.append((job_id, result))

    async def save_evaluation_error(
        self,
        job_id: str,
        error: str,
        evaluator_version: str,
        _claim_token: str,
    ) -> None:
        self.errors.append((job_id, error, evaluator_version))

    async def release_evaluation_claim(
        self, _job_id: str, _evaluator_version: str, _claim_token: str
    ) -> None:
        return None

    async def save_stage_a(self, *_args: object) -> None:
        raise AssertionError("legacy Stage A must not run")

    async def save_stage_b(self, *_args: object) -> None:
        raise AssertionError("legacy Stage B must not run")

    async def auto_decay(self, **kwargs: object) -> AutoDecayResult:
        self.auto_decay_calls.append(kwargs)
        return AutoDecayResult(ghosted=0, archived=0)

    async def record_step_timing(self, _timing: object) -> None:
        return None

    async def record_step_timings(self, _timings: object) -> None:
        return None


class _Ops:
    def __init__(self) -> None:
        self.usage_calls = 0

    async def get_cost(self, _day: str) -> None:
        return None

    async def record_cost(self, **_kwargs: object) -> None:
        return None

    async def record_llm_usage_with_cost(self, **_kwargs: object) -> None:
        self.usage_calls += 1


class _Renderer:
    def render_unified(self, **_kwargs: object) -> PromptBundle:
        return PromptBundle(messages=[], prompt_hash="prompt", resume_hash="resume")


class _LLM:
    def __init__(self) -> None:
        self.calls = 0

    async def complete(self, _request: object) -> LLMResponse:
        self.calls += 1
        content = json.dumps(
            {
                "summary": "Backend role requiring Python.",
                "eligibility_status": "pass",
                "eligibility_checks": [],
                "requirements": [
                    {
                        "requirement": "Python",
                        "priority": "required",
                        "category": "skill",
                        "match": "direct",
                        "resume_evidence": "Built Python services at work.",
                        "evidence_type": "professional",
                    }
                ],
                "match_tier": "strong_match",
                "one_line": "Direct professional evidence covers the requirement.",
                "ats_visibility_score": 90,
            }
        )
        return LLMResponse(
            content=content,
            model="mock-evaluator",
            input_tokens=10,
            output_tokens=20,
            cost_usd=0.01,
            cached=False,
            latency_ms=1,
        )


class _Logger:
    def info(self, _event: str, **_kwargs: object) -> None:
        return None

    def warning(self, _event: str, **_kwargs: object) -> None:
        return None

    def error(self, _event: str, **_kwargs: object) -> None:
        return None

    def debug(self, _event: str, **_kwargs: object) -> None:
        return None


def _job(  # noqa: PLR0913 - compact fixture builder
    id: str = "1",
    *,
    platform: str = "greenhouse",
    canonical_id: str | None = None,
    location: str = "Remote",
    jd_quality: QualityBand = QualityBand.FULL,
    title: str = "Software Engineer",
) -> JobPosting:
    body = "Build Python backend services. " * 12
    return JobPosting(
        id=id,
        platform=platform,
        canonical_id=canonical_id or f"job-{id}",
        url=f"https://example.com/job-{id}",
        title=title,
        company="Acme",
        location=location,
        discovered_at=datetime(2026, 8, 25, tzinfo=UTC),
        jd_text=body,
        jd_quality=jd_quality,
    )


def _service(
    store: _Store,
    ops: _Ops,
    llm: _LLM,
    *,
    logger: _Logger | None = None,
    hard_filters: HardFilters | None = None,
) -> EvaluateService:
    return EvaluateService(
        deps=EvaluateDependencies(
            store=store,  # type: ignore[arg-type]
            store_ops=ops,  # type: ignore[arg-type]
            store_status=store,  # type: ignore[arg-type]
            prompt_renderer=_Renderer(),  # type: ignore[arg-type]
            llm_stage_a=llm,  # type: ignore[arg-type]
            llm_stage_b=llm,  # type: ignore[arg-type]
            llm_evaluator=llm,  # type: ignore[arg-type]
            hard_filters=hard_filters,
        ),
        config=EvaluateRuntimeConfig(
            llm=EvaluateLLMConfig(
                stage_a="unused-a",
                stage_b="unused-b",
                evaluator="mock-evaluator",
                max_concurrent=1,
                max_daily_score_calls=10,
                max_daily_cost_usd=1.0,
            ),
            stage_a_threshold=60,
            resume_text="Built Python services at work.",
        ),
        logger=logger or _Logger(),  # type: ignore[arg-type]
    )


async def test_run_uses_one_llm_call_and_one_canonical_save() -> None:
    store = _Store(_job())
    ops = _Ops()
    llm = _LLM()
    service = _service(store, ops, llm)

    run = await service.run(corpus="unrated", limit=1)

    assert llm.calls == 1
    assert ops.usage_calls == 1
    assert len(store.saved) == 1
    assert store.saved[0][0] == "1"
    result = store.saved[0][1]
    assert result.match_tier == "strong_match"
    assert result.match_score == _FULL_MATCH
    assert run.jobs_scored == 1
    assert run.stage_a_scored == 0
    assert run.stage_b_scored == 0
    assert store.errors == []
    assert store.auto_decay_calls == [{"ghost_days": 30, "archive_ignored_days": 14}]


async def test_dry_run_previews_without_claim_or_llm_call() -> None:
    store = _Store(_job())
    ops = _Ops()
    llm = _LLM()

    run = await _service(store, ops, llm).run(corpus="unrated", limit=1, dry_run=True)

    assert [item.stage for item in run.dry_run_preview] == ["evaluation"]
    assert store.previews == 1
    assert store.claims == 0
    assert llm.calls == 0
    assert store.saved == []
    assert store.auto_decay_calls == []


def _filtered_twin_jobs() -> list[JobPosting]:
    return [
        _job("1", location="Blocked Country"),
        _job("2", platform="greenhouse", canonical_id="allowed-primary"),
        _job(
            "3",
            platform="indeed",
            canonical_id="allowed-duplicate",
            jd_quality=QualityBand.GOOD,
        ),
        _job(
            "4",
            canonical_id="unique",
            location="Detroit, MI",
            title="Data Engineer",
        ),
    ]


async def test_dry_run_filters_blocked_jobs_and_dedupes_twins() -> None:
    store = _Store(_filtered_twin_jobs())
    ops = _Ops()
    llm = _LLM()

    run = await _service(
        store,
        ops,
        llm,
        hard_filters=HardFilters(location_blocklist=["Blocked Country"]),
    ).run(corpus="unrated", limit=10, dry_run=True)

    assert [item.job_id for item in run.dry_run_preview] == ["2", "4"]
    assert run.jobs_filtered == 1
    assert store.claims == 0


async def test_real_run_claims_only_filtered_deduped_candidate_ids() -> None:
    store = _Store(_filtered_twin_jobs())
    ops = _Ops()
    llm = _LLM()

    await _service(
        store,
        ops,
        llm,
        hard_filters=HardFilters(location_blocklist=["Blocked Country"]),
    ).run(corpus="unrated", limit=10)

    assert store.claim_job_ids == [["2", "4"]]
    assert [job_id for job_id, _result in store.saved] == ["2", "4"]
    assert "1" not in store.claim_job_ids[0]
    assert "3" not in store.claim_job_ids[0]


async def test_short_jd_records_unified_error_without_llm_call() -> None:
    job = _job()
    job.jd_text = "Too short"
    store = _Store(job)
    ops = _Ops()
    llm = _LLM()

    run = await _service(store, ops, llm).run(limit=1)

    assert llm.calls == 0
    assert run.errors == 1
    assert store.saved == []
    assert store.errors[0][0] == "1"


async def test_eligibility_failure_forces_tier_without_zeroing_match() -> None:
    llm = _LLM()
    raw = json.loads((await llm.complete(object())).content)
    raw["eligibility_status"] = "fail"
    raw["eligibility_checks"] = [
        {
            "kind": "graduation_window",
            "requirement": "Graduate by June 2026",
            "status": "fail",
            "candidate_evidence": "Expected May 2027",
            "reason": "Graduation window does not match.",
        }
    ]
    raw["match_tier"] = "possible_match"

    result = parse_unified_evaluation_response(
        json.dumps(raw),
        model="mock",
        prompt_hash="prompt",
        resume_hash="resume",
    )

    assert result.eligibility_status == "fail"
    assert result.match_tier == "ineligible"
    assert result.match_score == _FULL_MATCH
