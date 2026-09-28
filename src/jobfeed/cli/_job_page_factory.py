"""Shared page interpretation runtime for browser-backed sources."""

from typing import Any

from jobfeed.adapters.llm._factory import LLMClientBuildOptions, build_llm_client
from jobfeed.adapters.llm._pricing import load_price_table
from jobfeed.services.job_page_extraction import JobPageExtractor


def build_page_extractor(app: Any) -> JobPageExtractor:
    """Build the configured page extraction client and persistence adapter.

    Args:
        app: Application dependencies and provider settings.

    Returns:
        Page extractor bound to the application configuration.
    """
    settings = app["settings"].llm
    secrets = app.get("provider_secrets")
    key = secrets.resolve("azure_openai") if secrets is not None else None
    client = build_llm_client(
        settings.stage_a,
        settings=settings,
        price_table=load_price_table(),
        logger=app["logger"],
        options=LLMClientBuildOptions(
            max_retries=0, api_key_overrides={"azure-openai": key} if key else {}
        ),
    )
    return JobPageExtractor(client, model=settings.stage_a, store=app["store"])
