"""Local-only endpoints for first-run and ongoing GUI configuration."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from jobfeed.config_editor import ConfigurationEditor, EditableConfiguration
from jobfeed.onboarding import OnboardingProviderService
from jobfeed.onboarding_resume import ResumeOnboardingService
from jobfeed.onboarding_searches import OnboardingSearchService
from jobfeed.web.deps import (
    get_configuration_editor,
    get_onboarding_provider_service,
    get_onboarding_resume_service,
    get_onboarding_search_service,
)
from jobfeed.web.errors import ApiError
from jobfeed.web.schemas.configuration import (
    ConfigurationResponse,
    configuration_response,
)
from jobfeed.web.schemas.onboarding import ApiErrorResponse

router = APIRouter()


class OnboardingFinishBody(BaseModel):
    """Final editable settings plus the disclosed usage-estimate input."""

    model_config = ConfigDict(extra="forbid")

    configuration: EditableConfiguration
    expected_jobs: int = Field(ge=1, le=100_000)


_BACKEND_BY_PROVIDER = {
    "openai_api": "openai-compat",
    "anthropic_api": "anthropic-api",
    "azure_openai": "azure-openai",
    "codex_cli": "codex-cli",
    "claude_cli": "claude-cli",
    "amazon_bedrock": "bedrock",
}


@router.get("/config")
async def get_configuration(
    editor: Annotated[ConfigurationEditor, Depends(get_configuration_editor)],
) -> ConfigurationResponse:
    """Return effective editable settings and onboarding state.

    Args:
        editor: Project-local configuration editor.

    Returns:
        Effective user-editable settings without database or secret values.
    """
    return configuration_response(editor.editable, configured=editor.is_configured)


@router.put("/config")
async def put_configuration(
    body: EditableConfiguration,
    editor: Annotated[ConfigurationEditor, Depends(get_configuration_editor)],
) -> ConfigurationResponse:
    """Atomically save valid settings and apply them to new work.

    Args:
        body: Complete GUI-managed configuration.
        editor: Project-local configuration editor.

    Returns:
        Saved effective settings with onboarding marked complete.
    """
    saved = editor.save(body)
    return configuration_response(
        EditableConfiguration.from_settings(saved), configured=True
    )


@router.post(
    "/onboarding/finish",
    responses={422: {"model": ApiErrorResponse}},
)
async def finish_onboarding(
    body: OnboardingFinishBody,
    editor: Annotated[ConfigurationEditor, Depends(get_configuration_editor)],
    provider_service: Annotated[
        OnboardingProviderService, Depends(get_onboarding_provider_service)
    ],
    resume_service: Annotated[
        ResumeOnboardingService, Depends(get_onboarding_resume_service)
    ],
    search_service: Annotated[
        OnboardingSearchService, Depends(get_onboarding_search_service)
    ],
) -> ConfigurationResponse:
    """Atomically apply the complete onboarding draft to active configuration.

    Args:
        body: Editable non-onboarding configuration fields.
        editor: Project-local configuration editor.
        provider_service: Verified provider and model draft.
        resume_service: Confirmed résumé and profile draft.
        search_service: Confirmed search selection draft.

    Returns:
        Saved effective configuration marked complete.

    Raises:
        ApiError: If any required onboarding step is incomplete.
    """
    try:
        provider = provider_service.state()
        resume = resume_service.state()
        searches = search_service.state()
        if (
            not provider.connected
            or provider.provider is None
            or provider.quick_model is None
            or provider.detailed_model is None
        ):
            raise ValueError("Connect a provider and choose both models")
        if (
            not resume.is_confirmed
            or resume.profile is None
            or resume.stored_name is None
        ):
            raise ValueError("Upload a résumé and confirm the job profile")
        enabled = [search for search in searches.searches if search.enabled]
        if not enabled:
            raise ValueError("Enable at least one job search")
        payload = body.configuration.model_dump(mode="json")
        payload["scoring"]["ml_gate_enabled"] = False
        payload["ml_gate"]["threshold_override"] = None
        backend = _BACKEND_BY_PROVIDER[provider.provider]
        payload["llm"].update(
            {
                "stage_a": f"{backend}/{provider.quick_model}",
                "stage_b": f"{backend}/{provider.detailed_model}",
                "master_resume_path": f"data/resumes/{resume.stored_name}",
            }
        )
        if provider.provider == "amazon_bedrock":
            payload["llm"].update(
                {
                    "bedrock_region": provider.region or "us-east-1",
                    "bedrock_profile": provider.profile,
                }
            )
        if provider.provider == "azure_openai":
            if provider.endpoint is None or not provider.deployment_pricing:
                raise ValueError(
                    "Azure OpenAI endpoint and confirmed deployment pricing "
                    "are required"
                )
            payload["llm"].update(
                {
                    "azure_openai_endpoint": provider.endpoint,
                    "azure_openai_api_key_env": "AZURE_OPENAI_API_KEY",
                    "azure_deployment_pricing": [
                        price.__dict__ for price in provider.deployment_pricing
                    ],
                }
            )
        for source in ("linkedin_guest", "indeed"):
            urls = [search.url for search in enabled if search.source == source]
            payload["sources"][source].update(
                {"enabled": bool(urls), "search_urls": urls}
            )
        payload["sources"]["ats"]["title_keywords"] = list(
            dict.fromkeys(search.query for search in enabled)
        )
        editable = EditableConfiguration.model_validate(payload)
    except (KeyError, ValueError) as exc:
        raise ApiError(422, "onboarding_incomplete", str(exc)) from exc

    saved = editor.save(editable)
    return configuration_response(
        EditableConfiguration.from_settings(saved), configured=True
    )


__all__ = ["router"]
