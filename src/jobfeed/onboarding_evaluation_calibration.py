"""Measure one production-shaped Quick and Detailed evaluation pair."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from jobfeed.adapters.llm._factory import LLMClientBuildOptions, build_llm_client
from jobfeed.adapters.llm._pricing import load_price_table
from jobfeed.adapters.llm._prompts import JinjaPromptRenderer
from jobfeed.adapters.llm.claude import ClaudeCliLLM
from jobfeed.adapters.llm.codex import CodexCliLLM
from jobfeed.config import LLMSettings
from jobfeed.domain.models import JobPosting, LLMRequest, LLMResponse
from jobfeed.observability import JobfeedLogger
from jobfeed.onboarding_plan_usage import (
    CodexPlanUsageReader,
    PlanUsageUnavailable,
)
from jobfeed.onboarding_resume_types import ResumeDraftState
from jobfeed.onboarding_types import ProviderOnboardingState
from jobfeed.ports.llm import LLMClient

_REPRESENTATIVE_STAGE_A_SCORE = 70


@dataclass(frozen=True, kw_only=True)
class MeasuredEvaluationCall:
    """Actual tokens, equivalent API cost, and latency for one model call."""

    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    latency_ms: int


@dataclass(frozen=True, kw_only=True)
class EvaluationCalibration:
    """Measured Quick + Detailed pair and surrounding subscription meter."""

    quick: MeasuredEvaluationCall
    detailed: MeasuredEvaluationCall
    allowance_before_percent: int | None
    allowance_after_percent: int | None


class OnboardingEvaluationCalibrator:
    """Run the real evaluation prompts without persisting a job or result."""

    def __init__(
        self,
        *,
        provider_state: Callable[[], ProviderOnboardingState],
        resume_state: Callable[[], ResumeDraftState],
        plan_usage_reader: CodexPlanUsageReader,
        logger: JobfeedLogger,
    ) -> None:
        self._provider_state = provider_state
        self._resume_state = resume_state
        self._plan_usage_reader = plan_usage_reader
        self._logger = logger

    async def calibrate(self, job_description: str) -> EvaluationCalibration:
        """Measure one Quick call and one Detailed call for the supplied JD.

        Args:
            job_description: Representative real job description.

        Returns:
            Measured calls and optional before/after subscription usage.

        Raises:
            ValueError: If setup is incomplete or pricing is unavailable.
        """
        provider = self._provider_state()
        resume = self._resume_state()
        if (
            provider.provider not in {"codex_cli", "claude_cli", "amazon_bedrock"}
            or not provider.connected
            or provider.quick_model is None
            or provider.detailed_model is None
        ):
            raise ValueError(
                "Connect a supported provider and choose both models first"
            )
        if resume.extracted_text is None or resume.profile is None:
            raise ValueError("Upload a résumé and confirm the job profile first")

        renderer = JinjaPromptRenderer(
            templates_dir=Path(__file__).resolve().parent / "templates"
        )
        job = _representative_job(job_description, resume)
        quick_bundle = renderer.render_stage_a(
            resume_text=resume.extracted_text,
            job=job,
        )
        detailed_bundle = renderer.render_stage_b(
            resume_text=resume.extracted_text,
            job=job,
            stage_a_score=_REPRESENTATIVE_STAGE_A_SCORE,
        )
        quick_client = self._client(
            provider=provider,
            model=provider.quick_model,
            timeout_s=120.0,
        )
        detailed_client = self._client(
            provider=provider,
            model=provider.detailed_model,
            timeout_s=210.0,
        )

        before = (
            await self._allowance_percent()
            if provider.provider == "codex_cli"
            else None
        )
        quick = await quick_client.complete(
            LLMRequest(messages=quick_bundle.messages, model=provider.quick_model)
        )
        detailed = await detailed_client.complete(
            LLMRequest(messages=detailed_bundle.messages, model=provider.detailed_model)
        )
        after = (
            await self._allowance_percent()
            if provider.provider == "codex_cli"
            else None
        )
        return EvaluationCalibration(
            quick=_measured(quick),
            detailed=_measured(detailed),
            allowance_before_percent=before,
            allowance_after_percent=after,
        )

    def _client(
        self, *, provider: ProviderOnboardingState, model: str, timeout_s: float
    ) -> LLMClient:
        if provider.provider == "amazon_bedrock":
            return build_llm_client(
                f"bedrock/{model}",
                settings=LLMSettings(
                    bedrock_region=provider.region or "us-east-1",
                    bedrock_profile=provider.profile,
                ),
                price_table=load_price_table(),
                logger=self._logger,
                options=LLMClientBuildOptions(timeout_s=timeout_s, max_retries=0),
            )
        if provider.provider == "claude_cli":
            return ClaudeCliLLM(
                model=model,
                timeout_s=timeout_s,
                logger=self._logger,
            )
        return CodexCliLLM(
            model=model,
            timeout_s=timeout_s,
            max_retries=0,
            price_table=load_price_table(),
            logger=self._logger,
        )

    async def _allowance_percent(self) -> int | None:
        try:
            return (await self._plan_usage_reader.read()).used_percent
        except PlanUsageUnavailable:
            return None


def _representative_job(job_description: str, resume: ResumeDraftState) -> JobPosting:
    assert resume.profile is not None
    profile = resume.profile
    return JobPosting(
        platform="onboarding-calibration",
        canonical_id="onboarding-calibration",
        url="https://jobfeed.local/onboarding-calibration",
        title=(
            profile.desired_titles[0] if profile.desired_titles else "Software Engineer"
        ),
        company="Representative employer",
        location=(
            profile.target_locations[0] if profile.target_locations else "Unspecified"
        ),
        discovered_at=datetime.now(UTC),
        jd_text=job_description,
    )


def _measured(response: LLMResponse) -> MeasuredEvaluationCall:
    if response.cost_usd is None:
        raise ValueError("The selected model does not have usable pricing")
    return MeasuredEvaluationCall(
        model=response.model,
        input_tokens=response.input_tokens,
        output_tokens=response.output_tokens,
        cost_usd=response.cost_usd,
        latency_ms=response.latency_ms,
    )


__all__ = [
    "EvaluationCalibration",
    "MeasuredEvaluationCall",
    "OnboardingEvaluationCalibrator",
]
