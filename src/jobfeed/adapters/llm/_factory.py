"""LLM factory — parse 'backend/model' spec and build the corresponding adapter."""

from __future__ import annotations

import os
import shutil
from collections.abc import Mapping
from dataclasses import dataclass, field

from jobfeed.adapters.llm._pricing import ModelPricing
from jobfeed.config import LLMSettings
from jobfeed.domain.errors import LLMRuntimeUnavailable
from jobfeed.observability import JobfeedLogger
from jobfeed.ports.llm import LLMClient


@dataclass(frozen=True)
class _UseSettingsTimeout:
    """Sentinel meaning the adapter should use the configured backend timeout."""


_USE_SETTINGS_TIMEOUT = _UseSettingsTimeout()


@dataclass(frozen=True, kw_only=True)
class LLMClientBuildOptions:
    """Per-client runtime policy overrides for LLM adapters."""

    timeout_s: float | _UseSettingsTimeout | None = _USE_SETTINGS_TIMEOUT
    max_retries: int = 2
    api_key_overrides: Mapping[str, str] = field(default_factory=dict, repr=False)


def build_llm_client(
    spec: str,
    *,
    settings: LLMSettings,
    price_table: dict[str, ModelPricing],
    logger: JobfeedLogger,
    options: LLMClientBuildOptions | None = None,
) -> LLMClient:
    """Parse ``backend/model`` spec and build the corresponding adapter.

    Args:
        spec: Provider routing string in ``backend/model`` format
            (e.g. ``codex-cli/gpt-5.6-luna``).
        settings: LLM runtime settings with per-backend timeouts.
        price_table: Pre-loaded model pricing table for cost estimation.
        logger: Structured logger for adapter-level events.
        options: Optional per-client timeout and retry overrides.

    Returns:
        Concrete ``LLMClient`` implementation ready for use.

    Raises:
        ValueError: If *spec* is not in ``backend/model`` format or the
            backend name is unrecognised, or a Codex model has no pricing.
        LLMRuntimeUnavailable: If the required CLI executable is not on PATH.
    """
    if "/" not in spec:
        raise ValueError(f"spec must be 'backend/model', got {spec!r}")

    backend, model_name = spec.split("/", 1)
    opts = options or LLMClientBuildOptions()

    if backend == "codex-cli":
        _require_executable("codex", backend)
        _require_codex_pricing(model_name, price_table)
        from jobfeed.adapters.llm.codex import CodexCliLLM  # noqa: PLC0415

        return CodexCliLLM(
            model=model_name,
            timeout_s=_resolve_timeout(settings.codex_timeout_s, opts),
            max_retries=opts.max_retries,
            price_table=price_table,
            logger=logger,
        )

    if backend == "claude-cli":
        _require_executable("claude", backend)
        from jobfeed.adapters.llm.claude import ClaudeCliLLM  # noqa: PLC0415

        return ClaudeCliLLM(
            model=model_name,
            timeout_s=_resolve_timeout(settings.claude_timeout_s, opts),
            max_retries=opts.max_retries,
            logger=logger,
        )

    if backend == "openai-compat":
        return _build_openai_compat(
            model_name,
            settings=settings,
            price_table=price_table,
            logger=logger,
            opts=opts,
        )

    if backend == "azure-openai":
        return _build_azure_openai(
            model_name,
            settings=settings,
            logger=logger,
            opts=opts,
        )

    if backend == "bedrock":
        return _build_bedrock(
            model_name,
            settings=settings,
            price_table=price_table,
            logger=logger,
            opts=opts,
        )

    if backend == "mock":
        from jobfeed.adapters.llm.mock import MockLLM  # noqa: PLC0415

        return MockLLM()

    raise ValueError(f"unknown LLM backend: {backend!r}")


def _build_openai_compat(
    model_name: str,
    *,
    settings: LLMSettings,
    price_table: dict[str, ModelPricing],
    logger: JobfeedLogger,
    opts: LLMClientBuildOptions,
) -> LLMClient:
    """Build an ``OpenAiCompatLLM`` from settings + an injected SDK client.

    The API key is validated BEFORE importing the SDK or opening any network
    connection.  Pricing is best-effort (Decision 9): no analogue of
    ``_require_codex_pricing``.
    """
    api_key = _resolve_api_key(
        settings.openai_compat_api_key_env,
        opts.api_key_overrides.get("openai-compat"),
        backend="openai-compat",
    )
    from openai import AsyncOpenAI  # noqa: PLC0415

    from jobfeed.adapters.llm.openai_compat import OpenAiCompatLLM  # noqa: PLC0415

    client = AsyncOpenAI(
        base_url=settings.openai_compat_base_url,
        api_key=api_key,
        timeout=_resolve_timeout(settings.openai_compat_timeout_s, opts),
        max_retries=opts.max_retries,
    )
    return OpenAiCompatLLM(
        client=client,
        model=model_name,
        price_table=price_table,
        logger=logger,
    )


def _build_azure_openai(
    deployment: str,
    *,
    settings: LLMSettings,
    logger: JobfeedLogger,
    opts: LLMClientBuildOptions,
) -> LLMClient:
    """Build the Azure v1 route over the shared OpenAI-compatible wire adapter."""
    endpoint = settings.azure_openai_endpoint
    if endpoint is None or not endpoint.strip():
        raise ValueError("azure-openai backend requires a configured endpoint")
    configured = {
        price.deployment: price for price in settings.azure_deployment_pricing
    }
    selected = configured.get(deployment)
    if selected is None:
        raise ValueError(
            f"azure-openai deployment {deployment!r} has no confirmed pricing"
        )
    api_key = _resolve_api_key(
        settings.azure_openai_api_key_env,
        opts.api_key_overrides.get("azure-openai"),
        backend="azure-openai",
    )
    from openai import AsyncOpenAI  # noqa: PLC0415

    from jobfeed.adapters.llm.openai_compat import OpenAiCompatLLM  # noqa: PLC0415

    per_million = 1_000_000
    deployment_prices = {
        deployment: ModelPricing(
            input_cost_per_token=selected.input_usd_per_million / per_million,
            output_cost_per_token=selected.output_usd_per_million / per_million,
            cached_input_cost_per_token=(
                selected.cached_input_usd_per_million / per_million
                if selected.cached_input_usd_per_million is not None
                else None
            ),
        )
    }
    client = AsyncOpenAI(
        base_url=endpoint,
        api_key=api_key,
        timeout=_resolve_timeout(settings.azure_openai_timeout_s, opts),
        max_retries=opts.max_retries,
    )
    return OpenAiCompatLLM(
        client=client,
        model=deployment,
        price_table=deployment_prices,
        logger=logger,
        request_profile=(
            "gpt5" if selected.base_model.lower().startswith("gpt-5") else "legacy"
        ),
    )


def _build_bedrock(
    model_name: str,
    *,
    settings: LLMSettings,
    price_table: dict[str, ModelPricing],
    logger: JobfeedLogger,
    opts: LLMClientBuildOptions,
) -> LLMClient:
    """Build a native Bedrock Converse client from the AWS credential chain."""
    import boto3  # noqa: PLC0415
    from botocore.config import Config  # noqa: PLC0415

    from jobfeed.adapters.llm.bedrock import BedrockLLM  # noqa: PLC0415

    timeout = _resolve_timeout(settings.bedrock_timeout_s, opts)
    config_args: dict[str, object] = {
        "retries": {"max_attempts": opts.max_retries + 1, "mode": "standard"},
    }
    if timeout is not None:
        config_args["read_timeout"] = timeout
    session = boto3.Session(profile_name=settings.bedrock_profile)
    client = session.client(
        "bedrock-runtime",
        region_name=settings.bedrock_region,
        config=Config(**config_args),
    )
    return BedrockLLM(
        client=client,
        model=model_name,
        region=settings.bedrock_region,
        price_table=price_table,
        logger=logger,
    )


def _require_codex_pricing(
    model_name: str,
    price_table: dict[str, ModelPricing],
) -> None:
    if model_name in price_table:
        return
    raise ValueError(
        f"codex-cli model {model_name!r} has no vendored pricing. "
        "Run `make update-prices` or choose a priced model before making "
        "real paid calls."
    )


def _resolve_api_key(
    env_name: str,
    fallback: str | None,
    *,
    backend: str,
) -> str:
    value = os.environ.get(env_name) or fallback
    if value:
        return value
    raise LLMRuntimeUnavailable(
        f"{backend} backend requires ${env_name}. Set it or reconnect the provider."
    )


def _require_executable(name: str, backend: str) -> None:
    """Assert that *name* is available on PATH.

    Args:
        name: Executable name to look up (e.g. ``codex``, ``claude``).
        backend: Backend label used in the error message.

    Raises:
        LLMRuntimeUnavailable: When the executable is not found.
    """
    if shutil.which(name) is None:
        raise LLMRuntimeUnavailable(
            f"{backend} backend requires '{name}' to be installed and on PATH. "
            "When using ./bin/jobfeed, install or configure it inside the "
            "jobfeed-cli runtime, or use a mock backend."
        )


def _resolve_timeout(
    configured_timeout: float,
    options: LLMClientBuildOptions,
) -> float | None:
    """Resolve a concrete timeout, preserving explicit unbounded None."""
    if isinstance(options.timeout_s, _UseSettingsTimeout):
        return configured_timeout
    return options.timeout_s


__all__ = [
    "LLMClientBuildOptions",
    "LLMRuntimeUnavailable",
    "build_llm_client",
]
