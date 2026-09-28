"""Shared page interpretation runtime for browser-backed sources."""

from typing import Any

from jobfeed.adapters.llm._factory import LLMClientBuildOptions, build_llm_client
from jobfeed.adapters.llm._pricing import load_price_table
from jobfeed.domain.models import LLMRequest, LLMResponse
from jobfeed.ports.llm import LLMClient
from jobfeed.services.job_page_extraction import JobPageExtractor


def build_page_extractor(app: Any) -> JobPageExtractor:
    """Build the configured page extraction client and persistence adapter.

    Args:
        app: Application dependencies and provider settings.

    Returns:
        Page extractor bound to the application configuration.
    """
    return JobPageExtractor(
        _DeferredPageClient(app), model=app["settings"].llm.stage_a, store=app["store"]
    )


class _DeferredPageClient:
    """Initialize the configured model only when a fallback needs it."""

    def __init__(self, app: Any) -> None:
        self._app = app
        self._client: LLMClient | None = None

    async def complete(self, request: LLMRequest) -> LLMResponse:
        """Forward a completion through the lazily initialized client.

        Args:
            request: Page-extraction request.

        Returns:
            The configured model's completion response.
        """
        if self._client is None:
            self._client = _build_client(self._app)
        return await self._client.complete(request)


def _build_client(app: Any) -> LLMClient:
    settings = app["settings"].llm
    secrets = app.get("provider_secrets")
    key = secrets.resolve("azure_openai") if secrets is not None else None
    return build_llm_client(
        settings.stage_a,
        settings=settings,
        price_table=load_price_table(),
        logger=app["logger"],
        options=LLMClientBuildOptions(
            max_retries=0, api_key_overrides={"azure-openai": key} if key else {}
        ),
    )
