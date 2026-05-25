"""LLM factory — parse 'backend/model' spec and build the corresponding adapter."""

from __future__ import annotations

import shutil

from jobfeed.adapters.llm._pricing import ModelPricing
from jobfeed.config import LLMSettings
from jobfeed.observability import JobfeedLogger
from jobfeed.ports.llm import LLMClient


class LLMRuntimeUnavailable(RuntimeError):
    """Required LLM CLI tool is not installed."""


def build_llm_client(
    spec: str,
    *,
    settings: LLMSettings,
    price_table: dict[str, ModelPricing],
    logger: JobfeedLogger,
) -> LLMClient:
    """Parse ``backend/model`` spec and build the corresponding adapter.

    Args:
        spec: Provider routing string in ``backend/model`` format
            (e.g. ``codex-cli/gpt-5.4-mini``).
        settings: LLM runtime settings with per-backend timeouts.
        price_table: Pre-loaded model pricing table for cost estimation.
        logger: Structured logger for adapter-level events.

    Returns:
        Concrete ``LLMClient`` implementation ready for use.

    Raises:
        ValueError: If *spec* is not in ``backend/model`` format or the
            backend name is unrecognised.
        LLMRuntimeUnavailable: If the required CLI executable is not on PATH.
    """
    if "/" not in spec:
        raise ValueError(f"spec must be 'backend/model', got {spec!r}")

    backend, model_name = spec.split("/", 1)

    if backend == "codex-cli":
        _require_executable("codex", backend)
        from jobfeed.adapters.llm.codex import CodexCliLLM  # noqa: PLC0415

        return CodexCliLLM(
            model=model_name,
            timeout_s=settings.codex_timeout_s,
            price_table=price_table,
            logger=logger,
        )

    if backend == "claude-cli":
        _require_executable("claude", backend)
        from jobfeed.adapters.llm.claude import ClaudeCliLLM  # noqa: PLC0415

        return ClaudeCliLLM(
            model=model_name,
            timeout_s=settings.claude_timeout_s,
            logger=logger,
        )

    if backend == "mock":
        from jobfeed.adapters.llm.mock import MockLLM  # noqa: PLC0415

        return MockLLM()

    raise ValueError(f"unknown LLM backend: {backend!r}")


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
            f"{backend} backend requires '{name}' to be installed and on PATH"
        )


__all__ = ["LLMRuntimeUnavailable", "build_llm_client"]
