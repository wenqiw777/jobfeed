"""Assemble provider and résumé onboarding workflows for the web process."""

from __future__ import annotations

from pathlib import Path

from jobfeed.observability import JobfeedLogger
from jobfeed.onboarding import OnboardingProviderService
from jobfeed.onboarding_companies import (
    CompanyRecommendationStore,
    OnboardingCompanyService,
)
from jobfeed.onboarding_profile_analyzer import OnboardingProfileAnalyzer
from jobfeed.onboarding_providers import ProviderChecker
from jobfeed.onboarding_resume import (
    ResumeDraftStore,
    ResumeFileStore,
    ResumeOnboardingService,
)
from jobfeed.onboarding_searches import OnboardingSearchService, OnboardingSearchStore
from jobfeed.onboarding_secrets import ProviderSecretStore
from jobfeed.onboarding_state import OnboardingDraftStore


def build_onboarding_services(
    project_root: Path,
    logger: JobfeedLogger,
    secrets: ProviderSecretStore | None = None,
) -> tuple[
    OnboardingProviderService,
    ResumeOnboardingService,
    OnboardingSearchService,
    OnboardingCompanyService,
]:
    """Build the resumable onboarding workflows over one local data root.

    Args:
        project_root: Directory containing ``config.toml`` and ``data``.
        logger: Shared structured application logger.

    Returns:
        Provider, résumé/profile, and search-selection workflows.
    """
    secrets = secrets or ProviderSecretStore(project_root / "data" / "secrets.toml")
    provider = OnboardingProviderService(
        checker=ProviderChecker(),
        secrets=secrets,
        drafts=OnboardingDraftStore(project_root / "data" / "onboarding.json"),
    )
    analyzer = OnboardingProfileAnalyzer(
        secrets=secrets,
        logger=logger,
        provider_state=provider.state,
    )
    resume = ResumeOnboardingService(
        files=ResumeFileStore(project_root / "data" / "resumes"),
        drafts=ResumeDraftStore(project_root / "data" / "onboarding-resume.json"),
        analyzer=analyzer,
        provider_state=provider.state,
    )
    searches = OnboardingSearchService(
        store=OnboardingSearchStore(project_root / "data" / "onboarding-searches.json"),
        resume_state=resume.state,
    )
    companies = OnboardingCompanyService(
        store=CompanyRecommendationStore(
            project_root / "data" / "onboarding-companies.json"
        ),
        recommender=analyzer,
        resume_state=resume.state,
        provider_state=provider.state,
    )
    return provider, resume, searches, companies


__all__ = ["build_onboarding_services"]
