"""Provider-backed résumé analysis for onboarding profile suggestions."""

from __future__ import annotations

import json
from dataclasses import dataclass

import httpx

from jobfeed.adapters.llm._pricing import load_price_table
from jobfeed.adapters.llm.claude import ClaudeCliLLM
from jobfeed.adapters.llm.codex import CodexCliLLM
from jobfeed.adapters.llm.openai_compat import OpenAiCompatLLM
from jobfeed.domain.models import LLMRequest, Message
from jobfeed.observability import JobfeedLogger
from jobfeed.onboarding_companies import (
    CompanyRecommendation,
    CompanyRecommendationBatch,
    parse_company_recommendations,
)
from jobfeed.onboarding_resume import parse_job_profile
from jobfeed.onboarding_resume_types import JobProfile
from jobfeed.onboarding_secrets import ProviderSecretStore
from jobfeed.onboarding_types import ProviderName

_SYSTEM_PROMPT = """You are an experienced recruiter creating a useful,
high-recall initial job-search profile from a résumé. Return only one JSON
object matching the supplied schema. Read the entire résumé and infer sensible
search suggestions from roles, projects, skills, education, dates, location,
and work authorization; do not require the résumé to state each preference
explicitly. Do not invent biographical facts or claim that an inferred search
suggestion was stated by the candidate.

Field rules:
- desired_titles: suggest 4-8 realistic title variants supported by the
  candidate's strongest experience and skills, in descending relevance order.
- seniority_levels: use only actual levels such as Intern, New graduate,
  Entry level, Mid level, Senior, Staff, Lead, or Manager. Infer them from
  graduation timing and depth of experience. Never put a job title in seniority_levels.
- target_countries and target_locations: derive from stated location and work
  authorization. Do not invent willingness to relocate.
- work_modes: use an explicitly stated preference; otherwise include all three
  work modes (remote, hybrid, and on-site) as broad search suggestions. Use
  only remote, hybrid, or on-site.
- industries: Infer 3-6 plausible industries from employers, projects, domains,
  and transferable technical skills instead of leaving the field empty.
- company_sizes: use a stated preference; otherwise include startup, mid-size, and large
  so the initial search does not exclude opportunities.
- work_authorization: preserve the precise stated authorization or sponsorship
  constraint.
- hiring_timeline: derive a concise window when graduation, availability, or
  relevant dates are present; otherwise use an empty string.
- excluded fields: include only explicit exclusions; otherwise use empty arrays.
- maximum_posting_age_hours: use 36 unless the résumé explicitly supports a
  different preference. Leave the legacy maximum_posting_age_days field null.
- resume_evidence: provide 5-10 short factual snippets covering the strongest
  evidence behind the suggestions. Keep evidence separate from inferred
  preferences and verbatim enough for the user to recognize.
"""

_COMPANY_SYSTEM_PROMPT = """You are an experienced technical recruiter.
Recommend 8-12 real companies that fit the confirmed job-search profile.
Return only one JSON object matching the supplied schema.

Choose a varied set ordered by likely fit. Respect target geography, work
authorization, desired seniority, industries, company sizes, and excluded
companies. Prefer employers that are plausible for the candidate's level and
roles. The slug must be the likely lowercase tenant slug used on a Greenhouse,
Lever, Ashby, Workable, SmartRecruiters, or Workday career board. Do not claim
that a board is supported: application code will probe every slug before the
user can add it. Keep each rationale to one short, profile-specific sentence.
"""


@dataclass(frozen=True, kw_only=True)
class _StructuredContract:
    system_prompt: str
    response_schema: dict[str, object]
    max_tokens: int


class OnboardingProfileAnalyzer:
    """Run one structured résumé analysis through the selected provider."""

    def __init__(
        self,
        *,
        secrets: ProviderSecretStore,
        logger: JobfeedLogger,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        """Create the analyzer with local secrets and optional shared HTTP."""
        self._secrets = secrets
        self._logger = logger
        self._http_client = http_client

    async def analyze(
        self,
        provider: ProviderName,
        model: str,
        resume_text: str,
    ) -> JobProfile:
        """Return a validated suggestion from the selected Detailed model.

        Args:
            provider: Verified onboarding provider.
            model: User-selected Detailed model.
            resume_text: Locally extracted résumé text.

        Returns:
            Complete validated job profile suggestion.

        Raises:
            ValueError: If a required key or valid structured response is absent.
        """
        prompt = _user_prompt(resume_text)
        if provider == "anthropic_api":
            content = await self._anthropic(
                model,
                prompt,
                system_prompt=_SYSTEM_PROMPT,
            )
        else:
            content = await self._completion(
                provider,
                model,
                prompt,
                contract=_StructuredContract(
                    system_prompt=_SYSTEM_PROMPT,
                    response_schema=JobProfile.model_json_schema(),
                    max_tokens=4096,
                ),
            )
        return parse_job_profile(content)

    async def recommend_companies(
        self,
        provider: ProviderName,
        model: str,
        profile: JobProfile,
    ) -> list[CompanyRecommendation]:
        """Return profile-derived candidates for subsequent ATS probing.

        Args:
            provider: Connected provider used for the structured request.
            model: Selected Detailed model.
            profile: Confirmed job-search profile.

        Returns:
            Validated provider suggestions awaiting ATS verification.
        """
        schema = CompanyRecommendationBatch.model_json_schema()
        prompt = (
            "Required JSON schema:\n"
            + json.dumps(schema, separators=(",", ":"))
            + "\n\nConfirmed job-search profile:\n"
            + profile.model_dump_json(indent=2)
        )
        if provider == "anthropic_api":
            content = await self._anthropic(
                model,
                prompt,
                system_prompt=_COMPANY_SYSTEM_PROMPT,
            )
        else:
            content = await self._completion(
                provider,
                model,
                prompt,
                contract=_StructuredContract(
                    system_prompt=_COMPANY_SYSTEM_PROMPT,
                    response_schema=schema,
                    max_tokens=3072,
                ),
            )
        return parse_company_recommendations(content)

    async def _completion(
        self,
        provider: ProviderName,
        model: str,
        prompt: str,
        *,
        contract: _StructuredContract,
    ) -> str:
        request = LLMRequest(
            messages=[
                Message(role="system", content=contract.system_prompt),
                Message(role="user", content=prompt),
            ],
            model=model,
            temperature=0.0,
            max_tokens=contract.max_tokens,
            response_schema=contract.response_schema,
        )
        prices = load_price_table()
        if provider == "codex_cli":
            codex_client = CodexCliLLM(
                model=model,
                timeout_s=120.0,
                max_retries=1,
                price_table=prices,
                logger=self._logger,
            )
            return (await codex_client.complete(request)).content
        if provider == "claude_cli":
            claude_client = ClaudeCliLLM(
                model=model,
                timeout_s=210.0,
                max_retries=1,
                logger=self._logger,
            )
            return (await claude_client.complete(request)).content
        key = self._require_key("openai_api")
        from openai import AsyncOpenAI  # noqa: PLC0415

        sdk = AsyncOpenAI(api_key=key, timeout=120.0, max_retries=1)
        try:
            openai_client = OpenAiCompatLLM(
                client=sdk,
                model=model,
                price_table=prices,
                logger=self._logger,
            )
            return (await openai_client.complete(request)).content
        finally:
            await sdk.close()

    async def _anthropic(
        self,
        model: str,
        prompt: str,
        *,
        system_prompt: str,
    ) -> str:
        headers = {
            "x-api-key": self._require_key("anthropic_api"),
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        payload = {
            "model": model,
            "max_tokens": 4096,
            "temperature": 0,
            "system": system_prompt,
            "messages": [{"role": "user", "content": prompt}],
        }
        if self._http_client is not None:
            response = await self._http_client.post(
                "https://api.anthropic.com/v1/messages",
                headers=headers,
                json=payload,
            )
        else:
            async with httpx.AsyncClient(timeout=120.0) as client:
                response = await client.post(
                    "https://api.anthropic.com/v1/messages",
                    headers=headers,
                    json=payload,
                )
        response.raise_for_status()
        return _anthropic_text(response.json())

    def _require_key(self, provider: ProviderName) -> str:
        value = self._secrets.resolve(provider)
        if value is None:
            raise ValueError("The selected provider API key is no longer available")
        return value


def _user_prompt(resume_text: str) -> str:
    schema = json.dumps(JobProfile.model_json_schema(), separators=(",", ":"))
    return f"Required JSON schema:\n{schema}\n\nRésumé text:\n{resume_text}"


def _anthropic_text(payload: object) -> str:
    if not isinstance(payload, dict) or not isinstance(payload.get("content"), list):
        raise ValueError("Anthropic returned an unreadable analysis response")
    parts = [
        str(item["text"])
        for item in payload["content"]
        if isinstance(item, dict)
        and item.get("type") == "text"
        and isinstance(item.get("text"), str)
    ]
    if not parts:
        raise ValueError("Anthropic returned an empty analysis response")
    return "\n".join(parts)


__all__ = ["OnboardingProfileAnalyzer"]
