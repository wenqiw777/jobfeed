"""Prompt contract for useful résumé-derived onboarding suggestions."""

from pathlib import Path

from jobfeed.config import LLMSettings
from jobfeed.domain.models import LLMResponse
from jobfeed.observability import get_logger
from jobfeed.onboarding_profile_analyzer import (
    _SYSTEM_PROMPT,
    OnboardingProfileAnalyzer,
    _user_prompt,
)
from jobfeed.onboarding_secrets import ProviderSecretStore
from jobfeed.onboarding_types import ProviderOnboardingState


def test_resume_analysis_prompt_requests_rich_search_profile() -> None:
    """The model gets field-specific guidance instead of defaulting to blanks."""
    prompt = _SYSTEM_PROMPT + _user_prompt("Graduates May 2027. Backend intern.")
    normalized = " ".join(prompt.split())

    assert "Never put a job title in seniority_levels" in normalized
    assert "descending relevance order" in normalized
    assert "Infer 3-6 plausible industries" in normalized
    assert "all three work modes" in normalized
    assert "startup, mid-size, and large" in normalized
    assert "Graduates May 2027. Backend intern." in prompt


async def test_bedrock_analysis_uses_saved_region_and_profile(
    tmp_path: Path, monkeypatch
) -> None:
    captured: list[tuple[str, str, str | None]] = []

    class FakeClient:
        async def complete(self, _request: object) -> LLMResponse:
            return LLMResponse(
                content=(
                    '{"desired_titles":["Platform Engineer"],'
                    '"seniority_levels":["Senior"],'
                    '"target_countries":["United States"],'
                    '"target_locations":["Detroit, MI"],'
                    '"work_modes":["hybrid"],'
                    '"industries":["Developer tools"],'
                    '"company_sizes":["mid-size"],'
                    '"work_authorization":"Authorized",'
                    '"hiring_timeline":"Available now",'
                    '"excluded_titles":[],"excluded_companies":[],'
                    '"excluded_locations":[],"excluded_keywords":[],'
                    '"maximum_posting_age_hours":36,'
                    '"maximum_posting_age_days":null,'
                    '"resume_evidence":["Built distributed systems"]}'
                ),
                model="us.anthropic.claude-sonnet-5",
                input_tokens=100,
                output_tokens=20,
                cost_usd=0.01,
                latency_ms=50,
            )

    def fake_build(
        spec: str, *, settings: LLMSettings, **_kwargs: object
    ) -> FakeClient:
        captured.append((spec, settings.bedrock_region, settings.bedrock_profile))
        return FakeClient()

    monkeypatch.setattr(
        "jobfeed.onboarding_profile_analyzer.build_llm_client", fake_build
    )
    analyzer = OnboardingProfileAnalyzer(
        secrets=ProviderSecretStore(tmp_path / "secrets.toml"),
        logger=get_logger(),
        provider_state=lambda: ProviderOnboardingState(
            provider="amazon_bedrock",
            connected=True,
            region="us-west-2",
            profile="jobfeed",
        ),
    )

    result = await analyzer.analyze(
        "amazon_bedrock", "us.anthropic.claude-sonnet-5", "Platform engineer."
    )

    assert result.desired_titles == ["Platform Engineer"]
    assert captured == [
        ("bedrock/us.anthropic.claude-sonnet-5", "us-west-2", "jobfeed")
    ]
