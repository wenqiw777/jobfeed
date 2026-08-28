"""Provider-specific onboarding evaluation calibration contracts."""

from __future__ import annotations

from typing import ClassVar

from jobfeed.config import LLMSettings
from jobfeed.domain.models import LLMResponse
from jobfeed.observability import get_logger
from jobfeed.onboarding_evaluation_calibration import OnboardingEvaluationCalibrator
from jobfeed.onboarding_resume_types import JobProfile, ResumeDraftState
from jobfeed.onboarding_types import ProviderOnboardingState


class _UnexpectedPlanUsageReader:
    async def read(self) -> object:
        raise AssertionError("Claude calibration must not read the Codex plan meter")


class _FakeClaudeClient:
    calls: ClassVar[list[str]] = []

    def __init__(self, *, model: str, **_kwargs: object) -> None:
        self._model = model

    async def complete(self, _request: object) -> LLMResponse:
        self.calls.append(self._model)
        detailed = "opus" in self._model
        return LLMResponse(
            content="{}",
            model=self._model,
            input_tokens=3_000 if detailed else 1_000,
            output_tokens=500 if detailed else 100,
            cost_usd=_DETAILED_COST_USD if detailed else _QUICK_COST_USD,
            latency_ms=4_500 if detailed else 1_500,
        )


async def test_claude_cli_calibration_uses_exact_reported_cost(monkeypatch) -> None:
    """Claude CLI measures both stages without pretending it has a live meter."""
    monkeypatch.setattr(
        "jobfeed.onboarding_evaluation_calibration.ClaudeCliLLM",
        _FakeClaudeClient,
    )
    _FakeClaudeClient.calls = []
    profile = JobProfile(
        desired_titles=["Platform Engineer"],
        seniority_levels=["Senior"],
        target_countries=["United States"],
        target_locations=["New York, NY"],
        work_modes=["hybrid"],
        industries=["Developer tools"],
        company_sizes=["mid-size"],
        work_authorization="Authorized",
        hiring_timeline="Available now",
        excluded_titles=[],
        excluded_companies=[],
        excluded_locations=[],
        excluded_keywords=[],
        maximum_posting_age_days=14,
        resume_evidence=["Built distributed systems"],
    )
    calibrator = OnboardingEvaluationCalibrator(
        provider_state=lambda: ProviderOnboardingState(
            provider="claude_cli",
            connected=True,
            quick_model="claude-sonnet-5",
            detailed_model="claude-opus-4-8",
        ),
        resume_state=lambda: ResumeDraftState(
            stored_name="resume.md",
            original_name="resume.md",
            extracted_text="Platform engineer with distributed systems experience.",
            profile=profile,
            is_confirmed=True,
        ),
        plan_usage_reader=_UnexpectedPlanUsageReader(),  # type: ignore[arg-type]
        logger=get_logger(),
    )

    result = await calibrator.calibrate("Representative platform job. " * 8)

    assert _FakeClaudeClient.calls == ["claude-sonnet-5", "claude-opus-4-8"]
    assert result.quick.cost_usd == _QUICK_COST_USD
    assert result.detailed.cost_usd == _DETAILED_COST_USD
    assert result.allowance_before_percent is None
    assert result.allowance_after_percent is None


_QUICK_COST_USD = 0.12
_DETAILED_COST_USD = 0.48


async def test_bedrock_calibration_uses_saved_region_and_profile(monkeypatch) -> None:
    """Bedrock calibration uses the same credential context as onboarding."""
    calls: list[tuple[str, str, str | None]] = []

    class FakeClient:
        def __init__(self, model: str) -> None:
            self._model = model

        async def complete(self, _request: object) -> LLMResponse:
            return LLMResponse(
                content="{}",
                model=self._model,
                input_tokens=100,
                output_tokens=20,
                cost_usd=0.01,
                latency_ms=50,
            )

    def fake_build(
        spec: str, *, settings: LLMSettings, **_kwargs: object
    ) -> FakeClient:
        calls.append((spec, settings.bedrock_region, settings.bedrock_profile))
        return FakeClient(spec.split("/", 1)[1])

    monkeypatch.setattr(
        "jobfeed.onboarding_evaluation_calibration.build_llm_client", fake_build
    )
    profile = JobProfile(
        desired_titles=["Platform Engineer"],
        seniority_levels=["Senior"],
        target_countries=["United States"],
        target_locations=["New York, NY"],
        work_modes=["hybrid"],
        industries=["Developer tools"],
        company_sizes=["mid-size"],
        work_authorization="Authorized",
        hiring_timeline="Available now",
        excluded_titles=[],
        excluded_companies=[],
        excluded_locations=[],
        excluded_keywords=[],
        maximum_posting_age_days=14,
        resume_evidence=["Built distributed systems"],
    )
    calibrator = OnboardingEvaluationCalibrator(
        provider_state=lambda: ProviderOnboardingState(
            provider="amazon_bedrock",
            connected=True,
            quick_model="us.anthropic.claude-haiku-4-5-20251001-v1:0",
            detailed_model="us.anthropic.claude-sonnet-5",
            region="us-west-2",
            profile="jobfeed",
        ),
        resume_state=lambda: ResumeDraftState(
            stored_name="resume.md",
            original_name="resume.md",
            extracted_text="Platform engineer.",
            profile=profile,
            is_confirmed=True,
        ),
        plan_usage_reader=_UnexpectedPlanUsageReader(),  # type: ignore[arg-type]
        logger=get_logger(),
    )

    result = await calibrator.calibrate("Representative platform job. " * 8)

    assert calls == [
        (
            "bedrock/us.anthropic.claude-haiku-4-5-20251001-v1:0",
            "us-west-2",
            "jobfeed",
        ),
        ("bedrock/us.anthropic.claude-sonnet-5", "us-west-2", "jobfeed"),
    ]
    assert result.allowance_before_percent is None
    assert result.allowance_after_percent is None
