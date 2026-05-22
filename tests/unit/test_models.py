"""Unit tests for the Phase 0 domain model contract."""

from __future__ import annotations

import ast
from dataclasses import fields, is_dataclass
from pathlib import Path

import pytest

from jobfeed.domain.models import (
    FitAnalysis,
    GapItem,
    JobEvaluation,
    JobPosting,
    JobStatus,
    LLMRequest,
    LLMResponse,
    LLMUsage,
    MatchItem,
    Message,
    MLGateResult,
    PipelineRun,
    QualityBand,
    SaveJobResult,
    StageAResult,
    StageBResult,
    Verdict,
)
from tests.support.factories import fixed_time

DEFAULT_MAX_TOKENS = 4096
EXPECTED_LATENCY_MS = 250


def test_enum_values_match_phase0_contract() -> None:
    """Enums should preserve the exact string values from the Phase 0 plan."""
    assert [band.value for band in QualityBand] == [
        "full",
        "good",
        "partial",
        "stub",
        "missing",
        "abandoned",
    ]
    assert [verdict.value for verdict in Verdict] == ["apply", "consider", "skip"]
    assert [status.value for status in JobStatus] == [
        "new",
        "scored",
        "shortlisted",
        "applied",
        "oa",
        "hr_call",
        "second_round",
        "final_round",
        "interviewing",
        "offer",
        "rejected",
        "ghosted",
        "archived",
        "ignored",
    ]


def test_domain_dataclasses_are_keyword_only() -> None:
    """Domain dataclasses should require explicit keyword construction."""
    dataclass_types = [
        JobPosting,
        StageAResult,
        StageBResult,
        FitAnalysis,
        MatchItem,
        GapItem,
        MLGateResult,
        LLMUsage,
        SaveJobResult,
        PipelineRun,
        Message,
        LLMRequest,
        LLMResponse,
        JobEvaluation,
    ]

    for dataclass_type in dataclass_types:
        assert is_dataclass(dataclass_type)
        assert all(field.kw_only for field in fields(dataclass_type))

    with pytest.raises(TypeError):
        MatchItem("Python", "Built ingestion services")  # type: ignore[call-arg]


def test_job_posting_defaults_and_natural_identity_fields() -> None:
    """JobPosting should be constructible before persistence."""
    job = JobPosting(
        platform="linkedin",
        canonical_id="abc123",
        url="https://example.com/jobs/abc123",
        title="Software Engineer Intern",
        company="Example Co",
        location="New York, NY",
        discovered_at=fixed_time(),
    )

    assert job.id is None
    assert (job.platform, job.canonical_id) == ("linkedin", "abc123")
    assert job.jd_text is None
    assert job.jd_quality is None
    assert job.posted_at is None
    assert job.enriched_at is None
    assert job.enrich_source is None


def test_pipeline_run_uses_zero_and_none_defaults() -> None:
    """PipelineRun counters should default to an empty run state."""
    run = PipelineRun(run_id="run-1", started_at=fixed_time(), source="scan")

    assert run.jobs_discovered == 0
    assert run.jobs_inserted == 0
    assert run.jobs_updated == 0
    assert run.jobs_filtered == 0
    assert run.jobs_ml_gated == 0
    assert run.stage_a_scored == 0
    assert run.stage_b_scored == 0
    assert run.jobs_scored == 0
    assert run.total_llm_cost_usd == 0.0
    assert run.errors == 0
    assert run.finished_at is None


def test_stage_b_result_preserves_nested_fit_analysis_shape() -> None:
    """StageBResult should expose normalized nested fit analysis fields."""
    strength = MatchItem(
        requirement="Python services",
        evidence="Built production Python data pipelines",
    )
    gap = GapItem(
        requirement="Kubernetes",
        severity="minor",
        mitigation="Can ramp from Docker deployment experience",
    )
    fit = FitAnalysis(score=87, strengths=[strength], gaps=[gap])

    result = StageBResult(
        verdict=Verdict.APPLY,
        jd_summary="Backend internship focused on data ingestion.",
        fit_analysis=fit,
        resume_hooks=["Python", "data pipelines"],
        model="mock/stage-b",
        prompt_hash="prompt-hash",
        resume_hash="resume-hash",
        raw_blocks={"block_c": {"score": 87}},
    )

    assert result.fit_analysis.strengths[0].evidence.startswith("Built")
    assert result.fit_analysis.gaps[0].severity == "minor"
    assert result.raw_blocks == {"block_c": {"score": 87}}


def test_score_models_reject_invalid_scores() -> None:
    """Score-bearing models should reject invalid score values."""
    with pytest.raises(ValueError, match="score must be an integer"):
        StageAResult(
            score=150,
            one_line="Too high",
            timing_eligible="unclear",
            model="mock/stage-a",
            prompt_hash="prompt-hash",
            resume_hash="resume-hash",
        )

    with pytest.raises(ValueError, match="score must be an integer"):
        FitAnalysis(score=-1, strengths=[], gaps=[])

    with pytest.raises(ValueError, match="score must be an integer"):
        StageAResult(
            score=True,  # type: ignore[arg-type]
            one_line="Bool is not an integer score",
            timing_eligible="unclear",
            model="mock/stage-a",
            prompt_hash="prompt-hash",
            resume_hash="resume-hash",
        )

    with pytest.raises(ValueError, match="score must be an integer"):
        FitAnalysis(score=85.5, strengths=[], gaps=[])  # type: ignore[arg-type]


def test_llm_request_and_response_defaults() -> None:
    """LLM request and response models should carry adapter-neutral defaults."""
    message = Message(role="user", content="Summarize this job.")
    request = LLMRequest(messages=[message], model="mock/stage-a")
    response = LLMResponse(
        content="{}",
        model="mock/stage-a",
        input_tokens=10,
        output_tokens=5,
    )

    assert request.temperature == 0.0
    assert request.max_tokens == DEFAULT_MAX_TOKENS
    assert request.response_schema is None
    assert response.cost_usd is None
    assert response.cached is False


def test_remaining_domain_models_are_instantiable() -> None:
    """Small result models should preserve their documented fields."""
    job = JobPosting(
        platform="greenhouse",
        canonical_id="456",
        url="https://example.com/jobs/456",
        title="Backend Intern",
        company="Example Co",
        location="Remote",
        discovered_at=fixed_time(),
        jd_quality=QualityBand.GOOD,
    )
    stage_a = StageAResult(
        score=78,
        one_line="Relevant backend internship.",
        timing_eligible="eligible",
        model="mock/stage-a",
        prompt_hash="prompt-hash",
        resume_hash="resume-hash",
    )

    assert MLGateResult(fit=True, probability=0.91).fail_reason is None
    assert (
        LLMUsage(
            model="mock/stage-a",
            input_tokens=100,
            output_tokens=50,
            cost_usd=0.01,
            cached=False,
            latency_ms=EXPECTED_LATENCY_MS,
            timestamp=fixed_time(),
        ).latency_ms
        == EXPECTED_LATENCY_MS
    )
    assert SaveJobResult(job_id="job-1", inserted=True, updated=False).inserted is True
    assert JobEvaluation(job=job, stage_a=stage_a, stage_b=None).stage_b is None


def test_models_module_imports_only_allowed_stdlib_dependencies() -> None:
    """The domain models module should remain free of adapter dependencies."""
    module_path = Path(__file__).resolve().parents[2] / "src/jobfeed/domain/models.py"
    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    imported_modules: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.update(alias.name for alias in node.names)
        if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            imported_modules.add(node.module)

    assert imported_modules <= {
        "__future__",
        "dataclasses",
        "datetime",
        "enum",
        "jobfeed.domain.types",
        "typing",
    }
