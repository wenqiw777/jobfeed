"""Unit tests for ClaudeCliLLM adapter — mock subprocess, no real CLI calls."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jobfeed.adapters.llm._subprocess import (
    SubprocessResult,
    SubprocessTimeout,
)
from jobfeed.adapters.llm.claude import ClaudeCliLLM
from jobfeed.domain.models import LLMRequest, LLMResponse, Message
from jobfeed.ports.llm import LLMClient

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MODEL = "claude-sonnet-4-20250514"
TIMEOUT_S = 210.0
MAX_RETRIES = 2
ELAPSED_MS = 1200

INPUT_TOKENS = 500
OUTPUT_TOKENS = 120
CACHE_READ_TOKENS = 80
TOTAL_COST_USD = 0.0042
DURATION_API_MS = 980

SYSTEM_CONTENT = "You are a job evaluator."
USER_CONTENT = "Evaluate this job posting."
RESULT_TEXT = "This role is a strong match for your background."


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_logger() -> MagicMock:
    """Return a mock satisfying the JobfeedLogger protocol."""
    logger = MagicMock()
    logger.info = MagicMock()
    logger.warning = MagicMock()
    logger.error = MagicMock()
    logger.debug = MagicMock()
    return logger


def _make_request() -> LLMRequest:
    """Build a standard two-message LLMRequest."""
    return LLMRequest(
        messages=[
            Message(role="system", content=SYSTEM_CONTENT),
            Message(role="user", content=USER_CONTENT),
        ],
        model=MODEL,
    )


def _default_envelope() -> dict[str, object]:
    """Return the default Claude CLI JSON envelope as a dict."""
    return {
        "result": RESULT_TEXT,
        "total_cost_usd": TOTAL_COST_USD,
        "duration_api_ms": DURATION_API_MS,
        "usage": {
            "input_tokens": INPUT_TOKENS,
            "output_tokens": OUTPUT_TOKENS,
            "cache_read_input_tokens": CACHE_READ_TOKENS,
        },
    }


def _make_envelope(**overrides: object) -> str:
    """Build a Claude CLI JSON envelope string with optional overrides.

    Top-level keys are replaced directly.  To override usage sub-keys,
    pass them as top-level kwargs — they are merged into the usage dict.
    """
    envelope = _default_envelope()
    usage_keys = {"input_tokens", "output_tokens", "cache_read_input_tokens"}
    usage = dict(envelope["usage"])  # type: ignore[arg-type]
    for key, value in overrides.items():
        if key in usage_keys:
            usage[key] = value
        else:
            envelope[key] = value
    envelope["usage"] = usage
    return json.dumps(envelope)


def _make_subprocess_result(stdout: str) -> SubprocessResult:
    """Build a SubprocessResult with given stdout."""
    return SubprocessResult(
        stdout=stdout,
        stderr="",
        returncode=0,
        elapsed_ms=ELAPSED_MS,
    )


def _make_adapter(logger: MagicMock | None = None) -> ClaudeCliLLM:
    """Build a ClaudeCliLLM with test defaults."""
    return ClaudeCliLLM(
        model=MODEL,
        timeout_s=TIMEOUT_S,
        max_retries=MAX_RETRIES,
        logger=logger or _make_logger(),
    )


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


async def test_happy_path_returns_correct_response() -> None:
    """Complete returns correct LLMResponse from JSON envelope."""
    stdout = _make_envelope()
    result = _make_subprocess_result(stdout)
    adapter = _make_adapter()

    with patch(
        "jobfeed.adapters.llm.claude.run_with_retry",
        AsyncMock(return_value=result),
    ):
        response = await adapter.complete(_make_request())

    assert isinstance(response, LLMResponse)
    assert response.content == RESULT_TEXT
    assert response.model == MODEL
    assert response.input_tokens == INPUT_TOKENS
    assert response.output_tokens == OUTPUT_TOKENS
    assert response.cost_usd == TOTAL_COST_USD
    assert response.latency_ms == DURATION_API_MS
    assert response.cached is True


# ---------------------------------------------------------------------------
# Protocol satisfaction
# ---------------------------------------------------------------------------


async def test_satisfies_llm_client_protocol() -> None:
    """ClaudeCliLLM is a runtime-checkable LLMClient."""
    adapter = _make_adapter()
    assert isinstance(adapter, LLMClient)


# ---------------------------------------------------------------------------
# Command construction
# ---------------------------------------------------------------------------


async def test_command_has_output_format_json_and_model() -> None:
    """Built command includes --output-format json and the configured model."""
    result = _make_subprocess_result(_make_envelope())
    mock_run = AsyncMock(return_value=result)
    adapter = _make_adapter()

    with patch("jobfeed.adapters.llm.claude.run_with_retry", mock_run):
        await adapter.complete(_make_request())

    cmd = mock_run.call_args[0][0]
    fmt_idx = cmd.index("--output-format")
    assert cmd[fmt_idx + 1] == "json"
    model_idx = cmd.index("--model")
    assert cmd[model_idx + 1] == MODEL


# ---------------------------------------------------------------------------
# System prompt via --system-prompt, user prompt as positional arg
# ---------------------------------------------------------------------------


async def test_system_prompt_and_user_prompt_in_command() -> None:
    """System prompt via --system-prompt flag, user prompt as positional arg."""
    result = _make_subprocess_result(_make_envelope())
    mock_run = AsyncMock(return_value=result)
    adapter = _make_adapter()

    with patch("jobfeed.adapters.llm.claude.run_with_retry", mock_run):
        await adapter.complete(_make_request())

    cmd = mock_run.call_args[0][0]
    sys_idx = cmd.index("--system-prompt")
    assert cmd[sys_idx + 1] == SYSTEM_CONTENT
    assert cmd[-1] == USER_CONTENT


# ---------------------------------------------------------------------------
# total_cost_usd propagation
# ---------------------------------------------------------------------------


async def test_total_cost_usd_propagated() -> None:
    """total_cost_usd from envelope is set as cost_usd on response."""
    custom_cost = 0.0099
    stdout = _make_envelope(total_cost_usd=custom_cost)
    result = _make_subprocess_result(stdout)
    adapter = _make_adapter()

    with patch(
        "jobfeed.adapters.llm.claude.run_with_retry",
        AsyncMock(return_value=result),
    ):
        response = await adapter.complete(_make_request())

    assert response.cost_usd == pytest.approx(custom_cost)


# ---------------------------------------------------------------------------
# duration_api_ms captured as latency_ms
# ---------------------------------------------------------------------------


async def test_duration_api_ms_captured_as_latency_ms() -> None:
    """duration_api_ms from envelope maps to response.latency_ms."""
    custom_latency = 1500
    stdout = _make_envelope(duration_api_ms=custom_latency)
    result = _make_subprocess_result(stdout)
    adapter = _make_adapter()

    with patch(
        "jobfeed.adapters.llm.claude.run_with_retry",
        AsyncMock(return_value=result),
    ):
        response = await adapter.complete(_make_request())

    assert response.latency_ms == custom_latency


# ---------------------------------------------------------------------------
# cache_read_input_tokens > 0 → cached=True
# ---------------------------------------------------------------------------


async def test_cache_read_tokens_positive_sets_cached_true() -> None:
    """cache_read_input_tokens > 0 sets cached=True on response."""
    stdout = _make_envelope(cache_read_input_tokens=50)
    result = _make_subprocess_result(stdout)
    adapter = _make_adapter()

    with patch(
        "jobfeed.adapters.llm.claude.run_with_retry",
        AsyncMock(return_value=result),
    ):
        response = await adapter.complete(_make_request())

    assert response.cached is True


async def test_cache_read_tokens_zero_sets_cached_false() -> None:
    """cache_read_input_tokens == 0 sets cached=False on response."""
    stdout = _make_envelope(cache_read_input_tokens=0)
    result = _make_subprocess_result(stdout)
    adapter = _make_adapter()

    with patch(
        "jobfeed.adapters.llm.claude.run_with_retry",
        AsyncMock(return_value=result),
    ):
        response = await adapter.complete(_make_request())

    assert response.cached is False


# ---------------------------------------------------------------------------
# Malformed JSON envelope → descriptive error
# ---------------------------------------------------------------------------


async def test_malformed_json_raises_value_error() -> None:
    """Non-JSON stdout from CLI raises ValueError with descriptive message."""
    result = _make_subprocess_result("not valid json at all")
    adapter = _make_adapter()

    with (
        patch(
            "jobfeed.adapters.llm.claude.run_with_retry",
            AsyncMock(return_value=result),
        ),
        pytest.raises(ValueError, match="Claude CLI returned malformed JSON"),
    ):
        await adapter.complete(_make_request())


# ---------------------------------------------------------------------------
# Timeout → SubprocessTimeout (via run_with_retry)
# ---------------------------------------------------------------------------


async def test_timeout_raises_subprocess_timeout() -> None:
    """Timeout propagates as SubprocessTimeout from run_with_retry."""
    adapter = _make_adapter()

    with (
        patch(
            "jobfeed.adapters.llm.claude.run_with_retry",
            AsyncMock(side_effect=SubprocessTimeout("claude timed out")),
        ),
        pytest.raises(SubprocessTimeout),
    ):
        await adapter.complete(_make_request())


# ---------------------------------------------------------------------------
# No stdin, no start_new_session
# ---------------------------------------------------------------------------


async def test_no_stdin_and_no_start_new_session() -> None:
    """SubprocessOptions has no input_text and start_new_session=False."""
    result = _make_subprocess_result(_make_envelope())
    mock_run = AsyncMock(return_value=result)
    adapter = _make_adapter()

    with patch("jobfeed.adapters.llm.claude.run_with_retry", mock_run):
        await adapter.complete(_make_request())

    opts = mock_run.call_args[1]["options"]
    assert opts.input_text is None
    assert opts.start_new_session is False
