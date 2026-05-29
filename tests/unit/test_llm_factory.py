"""Unit tests for LLM factory — provider routing without real CLI binaries."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from jobfeed.adapters.llm._factory import (
    LLMClientBuildOptions,
    LLMRuntimeUnavailable,
    build_llm_client,
)
from jobfeed.adapters.llm._pricing import load_price_table
from jobfeed.adapters.llm.claude import ClaudeCliLLM
from jobfeed.adapters.llm.codex import CodexCliLLM
from jobfeed.adapters.llm.mock import MockLLM
from jobfeed.config import LLMSettings
from jobfeed.observability import get_logger

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

CODEX_PATH = "/usr/local/bin/codex"
CLAUDE_PATH = "/usr/local/bin/claude"
DEFAULT_ADAPTER_RETRIES = 2


@pytest.fixture()
def settings() -> LLMSettings:
    return LLMSettings()


@pytest.fixture()
def price_table() -> dict:
    return load_price_table()


@pytest.fixture()
def logger():
    return get_logger()


# ---------------------------------------------------------------------------
# Codex backend
# ---------------------------------------------------------------------------


def test_codex_backend_returns_codex_cli_llm(settings, price_table, logger):
    """codex-cli/model builds a CodexCliLLM when the executable exists."""
    with patch("jobfeed.adapters.llm._factory.shutil.which", return_value=CODEX_PATH):
        client = build_llm_client(
            "codex-cli/gpt-5.4-mini",
            settings=settings,
            price_table=price_table,
            logger=logger,
        )

    assert isinstance(client, CodexCliLLM)
    assert client._max_retries == DEFAULT_ADAPTER_RETRIES


# ---------------------------------------------------------------------------
# Claude backend
# ---------------------------------------------------------------------------


def test_claude_backend_returns_claude_cli_llm(settings, price_table, logger):
    """claude-cli/model builds a ClaudeCliLLM when the executable exists."""
    with patch("jobfeed.adapters.llm._factory.shutil.which", return_value=CLAUDE_PATH):
        client = build_llm_client(
            "claude-cli/claude-haiku-4-5",
            settings=settings,
            price_table=price_table,
            logger=logger,
        )

    assert isinstance(client, ClaudeCliLLM)
    assert client._max_retries == DEFAULT_ADAPTER_RETRIES


def test_build_options_override_retry_and_timeout(settings, price_table, logger):
    """Factory should pass per-client retry and timeout policy to adapters."""
    with patch("jobfeed.adapters.llm._factory.shutil.which", return_value=CODEX_PATH):
        client = build_llm_client(
            "codex-cli/gpt-5.4-mini",
            settings=settings,
            price_table=price_table,
            logger=logger,
            options=LLMClientBuildOptions(timeout_s=None, max_retries=0),
        )

    assert isinstance(client, CodexCliLLM)
    assert client._timeout_s is None
    assert client._max_retries == 0


def test_codex_backend_rejects_unpriced_model(settings, logger):
    """codex-cli/model should fail before paid calls when pricing is unknown."""
    with (
        patch("jobfeed.adapters.llm._factory.shutil.which", return_value=CODEX_PATH),
        pytest.raises(ValueError, match="no vendored pricing"),
    ):
        build_llm_client(
            "codex-cli/not-in-price-table",
            settings=settings,
            price_table={},
            logger=logger,
        )


# ---------------------------------------------------------------------------
# Mock backend
# ---------------------------------------------------------------------------


def test_mock_backend_returns_mock_llm(settings, price_table, logger):
    """mock/stage-a builds a MockLLM without checking PATH."""
    client = build_llm_client(
        "mock/stage-a",
        settings=settings,
        price_table=price_table,
        logger=logger,
    )

    assert isinstance(client, MockLLM)


# ---------------------------------------------------------------------------
# Error cases
# ---------------------------------------------------------------------------


def test_unknown_backend_raises_value_error(settings, price_table, logger):
    """An unrecognised backend name raises ValueError."""
    with pytest.raises(ValueError, match="unknown LLM backend"):
        build_llm_client(
            "unknown/model",
            settings=settings,
            price_table=price_table,
            logger=logger,
        )


def test_missing_slash_raises_value_error(settings, price_table, logger):
    """A spec without a slash separator raises ValueError."""
    with pytest.raises(ValueError, match="spec must be 'backend/model'"):
        build_llm_client(
            "no-slash",
            settings=settings,
            price_table=price_table,
            logger=logger,
        )


def test_codex_missing_executable_raises_runtime_unavailable(
    settings, price_table, logger
):
    """codex-cli backend raises LLMRuntimeUnavailable when codex is not on PATH."""
    with (
        patch("jobfeed.adapters.llm._factory.shutil.which", return_value=None),
        pytest.raises(LLMRuntimeUnavailable, match="codex-cli backend requires"),
    ):
        build_llm_client(
            "codex-cli/gpt-5.4-mini",
            settings=settings,
            price_table=price_table,
            logger=logger,
        )


def test_claude_missing_executable_raises_runtime_unavailable(
    settings, price_table, logger
):
    """claude-cli backend raises LLMRuntimeUnavailable when claude is not on PATH."""
    with (
        patch("jobfeed.adapters.llm._factory.shutil.which", return_value=None),
        pytest.raises(LLMRuntimeUnavailable, match="claude-cli backend requires"),
    ):
        build_llm_client(
            "claude-cli/claude-haiku-4-5",
            settings=settings,
            price_table=price_table,
            logger=logger,
        )
