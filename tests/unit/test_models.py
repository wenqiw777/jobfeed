"""Unit tests for the Phase 0 domain model contract."""

from __future__ import annotations

import ast
import subprocess
import sys
from dataclasses import fields, is_dataclass
from pathlib import Path

import pytest

from jobfeed.domain.models import (
    ApplicationRecord,
    ApplicationStats,
    AttentionItem,
    AttentionReport,
    AutoDecayResult,
    CompanyRecord,
    CostEntry,
    DigestStats,
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
    ResumeSnapshot,
    ResumeVariant,
    ResumeVariantStats,
    SaveJobResult,
    StageAResult,
    StageBResult,
    StatusInfo,
    StatusTransition,
    Verdict,
    WorkflowAttention,
    WorkflowAttentionItem,
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
        StatusTransition,
        StatusInfo,
        AutoDecayResult,
        WorkflowAttentionItem,
        WorkflowAttention,
        ApplicationRecord,
        ResumeSnapshot,
        ResumeVariant,
        ResumeVariantStats,
        ApplicationStats,
        CompanyRecord,
        CostEntry,
        DigestStats,
        AttentionItem,
        AttentionReport,
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

    assert MLGateResult(score=0.91, result="pass").fail_reason is None
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


def test_ml_gate_result_rejects_out_of_range_score() -> None:
    """A score outside [0, 1] is rejected at construction."""
    with pytest.raises(ValueError, match="out of range"):
        MLGateResult(score=1.4, result="pass")


def test_ml_gate_result_rejects_unknown_result() -> None:
    """A result other than pass/fail is rejected at construction."""
    with pytest.raises(ValueError, match="pass"):
        MLGateResult(score=0.5, result="maybe")


def test_models_status_is_independently_importable() -> None:
    """domain.models_status must import standalone (no models re-export cycle)."""
    result = subprocess.run(
        [sys.executable, "-c", "import jobfeed.domain.models_status"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


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

    allowed = {
        "__future__",
        "dataclasses",
        "datetime",
        "enum",
        "jobfeed.domain.models_application",
        "jobfeed.domain.models_llm",
        "jobfeed.domain.models_ops",
        "jobfeed.domain.models_status",
        "jobfeed.domain.types",
        "typing",
    }
    assert imported_modules <= allowed


def test_status_transition_captures_from_to() -> None:
    """StatusTransition records from/to with optional reason."""
    t = StatusTransition(
        job_id="1",
        from_status=JobStatus.SCORED,
        to_status=JobStatus.SHORTLISTED,
        changed_at=fixed_time(),
    )
    assert t.from_status == JobStatus.SCORED
    assert t.to_status == JobStatus.SHORTLISTED
    assert t.reason is None
    assert t.resume_variant is None


def test_status_info_represents_current_state() -> None:
    """StatusInfo should carry all current-status fields."""
    info = StatusInfo(
        job_id="1",
        status=JobStatus.APPLIED,
        last_status_change_at=fixed_time(),
        next_followup_at=fixed_time(),
        resume_variant="v2",
        notes="Sent follow-up",
    )
    assert info.status == JobStatus.APPLIED
    assert info.resume_variant == "v2"


def test_application_record_has_all_audit_fields() -> None:
    """ApplicationRecord should carry all snapshot fields."""
    rec = ApplicationRecord(
        job_id="1",
        applied_at=fixed_time(),
        master_resume_hash="aa" * 32,
        verdict_snapshot='{"verdict": "apply"}',
        fit_snapshot='{"score": 80}',
        hooks_snapshot='{"hooks": []}',
    )
    assert rec.verdict_snapshot is not None
    assert rec.fit_snapshot is not None
    assert rec.hooks_snapshot is not None
    assert rec.tailored_resume_hash is None
    assert rec.cover_letter is None
    assert rec.notes is None


def test_resume_snapshot_validates_hex_hash() -> None:
    """ResumeSnapshot should reject non-hex or empty hashes."""
    ResumeSnapshot(
        resume_hash="abcdef0123456789" * 4,
        captured_at=fixed_time(),
        source="master",
        content="resume content",
    )

    with pytest.raises(ValueError, match="non-empty hex"):
        ResumeSnapshot(
            resume_hash="",
            captured_at=fixed_time(),
            source="master",
            content="resume content",
        )

    with pytest.raises(ValueError, match="non-empty hex"):
        ResumeSnapshot(
            resume_hash="not-hex!",
            captured_at=fixed_time(),
            source="master",
            content="resume content",
        )


def test_company_record_defaults() -> None:
    """CompanyRecord should default to zeroed counters."""
    c = CompanyRecord(slug="acme")
    assert c.ats_vendor is None
    assert c.ats_override is False
    assert c.job_count_last_scan == 0
    assert c.consecutive_discover_failures == 0


EXPECTED_CALLS = 10
EXPECTED_GHOSTED = 5
EXPECTED_ARCHIVED = 3


def test_cost_entry_fields() -> None:
    """CostEntry should carry all ledger fields."""
    entry = CostEntry(
        day="2026-05-22",
        spent_usd=1.50,
        calls=EXPECTED_CALLS,
        last_updated=fixed_time(),
    )
    assert entry.day == "2026-05-22"
    assert entry.calls == EXPECTED_CALLS


def test_auto_decay_result() -> None:
    """AutoDecayResult should carry ghosted and archived counts."""
    r = AutoDecayResult(ghosted=EXPECTED_GHOSTED, archived=EXPECTED_ARCHIVED)
    assert r.ghosted == EXPECTED_GHOSTED
    assert r.archived == EXPECTED_ARCHIVED


def test_workflow_attention_item_has_url() -> None:
    """WorkflowAttentionItem must include url for digest rendering."""
    item = WorkflowAttentionItem(
        job_id="1",
        title="SWE Intern",
        company="Acme",
        url="https://example.com/jobs/1",
        status="applied",
        last_status_change_at=fixed_time(),
        next_followup_at=None,
        notes=None,
        reason="follow-up due",
        days_since=3,
    )
    assert item.url == "https://example.com/jobs/1"


EXPECTED_TOTAL_JOBS = 100
EXPECTED_VARIANT_SENT = 10


def test_digest_stats_fields() -> None:
    """DigestStats should carry all aggregate fields."""
    s = DigestStats(
        total_jobs=EXPECTED_TOTAL_JOBS,
        scored_today=5,
        stage_b_evaluated=50,
        filtered_count=30,
        llm_calls_today=10,
        total_cost_today_usd=0.50,
    )
    assert s.total_jobs == EXPECTED_TOTAL_JOBS


def test_attention_report_defaults_to_empty() -> None:
    """AttentionReport lists should default to empty."""
    report = AttentionReport()
    assert report.enrich_errors == []
    assert report.low_quality_scored == []


def test_application_stats_with_resume_breakdown() -> None:
    """ApplicationStats should carry optional per-variant breakdown."""
    variant = ResumeVariantStats(
        sent=EXPECTED_VARIANT_SENT,
        responses=5,
        interviews=3,
        offers=1,
        rejections=1,
    )
    stats = ApplicationStats(
        applied_count=EXPECTED_VARIANT_SENT,
        response_count=5,
        interview_count=3,
        offer_count=1,
        rejection_count=1,
        median_days_to_response=7.0,
        by_resume={"v1": variant},
    )
    assert stats.by_resume is not None
    assert stats.by_resume["v1"].sent == EXPECTED_VARIANT_SENT


def _check_submodule_imports(filename: str, allowed: set[str]) -> None:
    module_path = Path(__file__).resolve().parents[2] / "src/jobfeed/domain" / filename
    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            imported.add(node.module)
    assert imported <= allowed, f"{filename}: unexpected imports {imported - allowed}"


def test_submodules_import_only_stdlib_and_domain() -> None:
    """All domain sub-modules must stay stdlib + jobfeed.domain.* only."""
    base = {"__future__", "dataclasses", "datetime", "typing"}
    _check_submodule_imports(
        "models_status.py",
        base | {"jobfeed.domain.models"},
    )
    _check_submodule_imports("models_application.py", base)
    _check_submodule_imports("models_ops.py", base)
    _check_submodule_imports(
        "status.py",
        base | {"jobfeed.domain.models"},
    )
