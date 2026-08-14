"""LLM factory — parse 'backend/model' spec and build the corresponding adapter."""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass

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
    _require_api_key(settings.openai_compat_api_key_env)
    from openai import AsyncOpenAI  # noqa: PLC0415

    from jobfeed.adapters.llm.openai_compat import OpenAiCompatLLM  # noqa: PLC0415

    client = AsyncOpenAI(
        base_url=settings.openai_compat_base_url,
        api_key=os.environ[settings.openai_compat_api_key_env],
        timeout=_resolve_timeout(settings.openai_compat_timeout_s, opts),
        max_retries=opts.max_retries,
    )
    return OpenAiCompatLLM(
        client=client,
        model=model_name,
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


def _require_api_key(env_name: str) -> None:
    """Assert that the configured API-key env var is set and non-empty.

    Parallels ``_require_executable``: raised BEFORE the SDK import or any
    network call so a misconfigured ``openai-compat`` backend fails fast with a
    teaching message instead of a vendor authentication error.

    Args:
        env_name: Name of the env var that holds the API key.

    Raises:
        LLMRuntimeUnavailable: When the env var is unset or empty.
    """
    if os.environ.get(env_name):
        return
    raise LLMRuntimeUnavailable(
        f"openai-compat backend requires ${env_name}. Set it, or use "
        "claude-cli if you have the CLI logged in."
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
