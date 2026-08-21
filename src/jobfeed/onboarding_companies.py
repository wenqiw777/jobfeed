"""Profile-derived company recommendations persisted as onboarding draft state."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from jobfeed.domain.company_slug import is_valid_slug, normalize_slug
from jobfeed.onboarding_resume_files import _write_private_bytes
from jobfeed.onboarding_resume_types import JobProfile, ResumeDraftState
from jobfeed.onboarding_types import ProviderName, ProviderOnboardingState

_RECOMMENDER_VERSION = "v1-profile-company-recommendations"
_MIN_FENCED_LINES = 3


class CompanyRecommendation(BaseModel):
    """One model-suggested company candidate awaiting a real ATS probe."""

    model_config = ConfigDict(extra="forbid")

    name: str
    slug: str
    rationale: str

    @field_validator("name", "rationale")
    @classmethod
    def _non_blank_text(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Company name and rationale must not be blank")
        return cleaned

    @field_validator("slug")
    @classmethod
    def _valid_slug(cls, value: str) -> str:
        cleaned = normalize_slug(value)
        if not is_valid_slug(cleaned):
            raise ValueError("Company board slug is invalid")
        return cleaned


class CompanyRecommendationBatch(BaseModel):
    """Strict provider response shape for candidate generation."""

    model_config = ConfigDict(extra="forbid")

    recommendations: list[CompanyRecommendation] = Field(min_length=6, max_length=12)


class CompanyRecommendationState(BaseModel):
    """Provider/profile-bound resumable recommendation draft."""

    model_config = ConfigDict(extra="forbid")

    profile_fingerprint: str | None = None
    recommendations: list[CompanyRecommendation] = Field(default_factory=list)


class CatalogCompany(BaseModel):
    """One canonical company/vendor pair extracted from a real ATS URL."""

    model_config = ConfigDict(extra="forbid")

    slug: str
    vendor: Literal["greenhouse", "ashby", "lever"]

    @field_validator("slug")
    @classmethod
    def _valid_slug(cls, value: str) -> str:
        cleaned = normalize_slug(value)
        if not is_valid_slug(cleaned):
            raise ValueError("Company board slug is invalid")
        return cleaned


class CompanyCatalogState(BaseModel):
    """Broad public-job-list catalog available for one-click bulk addition."""

    model_config = ConfigDict(extra="forbid")

    source_counts: dict[str, int]
    companies: list[CatalogCompany]


class CompanyRecommender(Protocol):
    """Capability that proposes likely company-board slugs from a profile."""

    async def recommend_companies(
        self,
        provider: ProviderName,
        model: str,
        profile: JobProfile,
    ) -> list[CompanyRecommendation]:
        """Return candidates that still require a real ATS probe.

        Args:
            provider: Connected AI provider.
            model: Selected Detailed model.
            profile: Confirmed job profile.

        Returns:
            Suggested companies for subsequent ATS verification.
        """
        ...


class CompanyRecommendationStore:
    """Persist secret-free company candidates under the local data directory."""

    def __init__(self, path: Path) -> None:
        self._path = path.resolve()

    def load(self) -> CompanyRecommendationState:
        """Return the current validated draft or an empty state.

        Returns:
            Persisted recommendation state, or a fresh empty state.
        """
        if not self._path.exists():
            return CompanyRecommendationState()
        return CompanyRecommendationState.model_validate_json(
            self._path.read_text(encoding="utf-8")
        )

    def save(
        self,
        recommendations: list[CompanyRecommendation],
        fingerprint: str,
    ) -> CompanyRecommendationState:
        """Persist candidates bound to one provider/profile fingerprint.

        Args:
            recommendations: Validated company suggestions.
            fingerprint: Provider, model, and profile identity hash.

        Returns:
            Newly persisted recommendation state.
        """
        state = CompanyRecommendationState(
            profile_fingerprint=fingerprint,
            recommendations=recommendations,
        )
        _write_private_bytes(
            self._path,
            (state.model_dump_json(indent=2) + "\n").encode(),
        )
        return state


class OnboardingCompanyService:
    """Generate and resume company candidates after profile confirmation."""

    def __init__(
        self,
        *,
        store: CompanyRecommendationStore,
        recommender: CompanyRecommender,
        resume_state: Callable[[], ResumeDraftState],
        provider_state: Callable[[], ProviderOnboardingState],
    ) -> None:
        self._store = store
        self._recommender = recommender
        self._resume_state = resume_state
        self._provider_state = provider_state

    async def recommend(self, *, refresh: bool = False) -> CompanyRecommendationState:
        """Return cached candidates or generate them with the Detailed model.

        Args:
            refresh: Whether to bypass a matching saved recommendation set.

        Returns:
            Profile-bound recommendation state.
        """
        profile = self._confirmed_profile()
        provider = self._connected_provider()
        assert provider.provider is not None
        assert provider.detailed_model is not None
        fingerprint = _recommendation_fingerprint(
            profile,
            provider.provider,
            provider.detailed_model,
        )
        current = self._store.load()
        if (
            not refresh
            and current.profile_fingerprint == fingerprint
            and current.recommendations
        ):
            return current
        recommendations = await self._recommender.recommend_companies(
            provider.provider,
            provider.detailed_model,
            profile,
        )
        kept = _deduplicate_and_filter(recommendations, profile.excluded_companies)
        return self._store.save(kept, fingerprint)

    def _confirmed_profile(self) -> JobProfile:
        state = self._resume_state()
        if not state.is_confirmed or state.profile is None:
            raise ValueError("Confirm the job profile before selecting companies")
        return state.profile

    def _connected_provider(self) -> ProviderOnboardingState:
        state = self._provider_state()
        if (
            not state.connected
            or state.provider is None
            or state.detailed_model is None
        ):
            raise ValueError("Complete provider setup before recommending companies")
        return state


def parse_company_recommendations(content: str) -> list[CompanyRecommendation]:
    """Parse one strict JSON recommendation response from a provider.

    Args:
        content: Raw provider response, optionally in a fenced JSON block.

    Returns:
        Validated company recommendations.

    Raises:
        ValueError: If the response is not valid recommendation JSON.
    """
    candidate = content.strip()
    if candidate.startswith("```"):
        lines = candidate.splitlines()
        if len(lines) >= _MIN_FENCED_LINES and lines[-1].strip() == "```":
            candidate = "\n".join(lines[1:-1]).strip()
    try:
        payload = json.loads(candidate)
        return CompanyRecommendationBatch.model_validate(payload).recommendations
    except (json.JSONDecodeError, ValidationError, TypeError) as exc:
        raise ValueError(
            "The provider did not return valid company recommendations"
        ) from exc


def _recommendation_fingerprint(
    profile: JobProfile,
    provider: ProviderName,
    model: str,
) -> str:
    payload = (
        f"{_RECOMMENDER_VERSION}\0{provider}\0{model}\0{profile.model_dump_json()}"
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def _deduplicate_and_filter(
    recommendations: list[CompanyRecommendation],
    excluded_companies: list[str],
) -> list[CompanyRecommendation]:
    excluded = {value.casefold() for value in excluded_companies}
    seen: set[str] = set()
    kept: list[CompanyRecommendation] = []
    for recommendation in recommendations:
        if (
            recommendation.slug in seen
            or recommendation.slug.casefold() in excluded
            or recommendation.name.casefold() in excluded
        ):
            continue
        seen.add(recommendation.slug)
        kept.append(recommendation)
    return kept


__all__ = [
    "CatalogCompany",
    "CompanyCatalogState",
    "CompanyRecommendation",
    "CompanyRecommendationBatch",
    "CompanyRecommendationState",
    "CompanyRecommendationStore",
    "OnboardingCompanyService",
    "parse_company_recommendations",
]
