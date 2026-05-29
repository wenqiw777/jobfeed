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
CACHE_CREATION_TOKENS = 40
TOTAL_COST_USD = 0.0042
DURATION_API_MS = 980

SYSTEM_CONTENT = "You are a job evaluator."
USER_CONTENT = "Evaluate this job posting."
RESULT_TEXT = "This role is a strong match for your background."
CHARGED_MODEL = "claude-sonnet-4-6-20260514"


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
        "modelUsage": {
            CHARGED_MODEL: {
                "inputTokens": INPUT_TOKENS,
                "outputTokens": OUTPUT_TOKENS,
                "costUSD": TOTAL_COST_USD,
            }
        },
    }


def _make_envelope(**overrides: object) -> str:
    """Build a Claude CLI JSON envelope string with optional overrides.

    Top-level keys are replaced directly.  To override usage sub-keys,
    pass them as top-level kwargs — they are merged into the usage dict.
    """
    envelope = _default_envelope()
    usage_keys = {
        "input_tokens",
        "output_tokens",
        "cache_creation_input_tokens",
        "cache_read_input_tokens",
    }
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
    assert response.model == CHARGED_MODEL
    assert response.input_tokens == INPUT_TOKENS + CACHE_READ_TOKENS
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
    # --setting-sources "" isolates settings/hooks while still allowing OAuth.
    sources_idx = cmd.index("--setting-sources")
    assert cmd[sources_idx + 1] == ""
    assert "--bare" not in cmd
    fmt_idx = cmd.index("--output-format")
    assert cmd[fmt_idx + 1] == "json"
    input_idx = cmd.index("--input-format")
    assert cmd[input_idx + 1] == "text"
    model_idx = cmd.index("--model")
    assert cmd[model_idx + 1] == MODEL


# ---------------------------------------------------------------------------
# System prompt via --system-prompt, user prompt via stdin
# ---------------------------------------------------------------------------


async def test_system_prompt_in_command_and_user_prompt_on_stdin() -> None:
    """System prompt uses --system-prompt and user prompt uses stdin."""
    result = _make_subprocess_result(_make_envelope())
    mock_run = AsyncMock(return_value=result)
    adapter = _make_adapter()

    with patch("jobfeed.adapters.llm.claude.run_with_retry", mock_run):
        await adapter.complete(_make_request())

    cmd = mock_run.call_args[0][0]
    sys_idx = cmd.index("--system-prompt")
    assert cmd[sys_idx + 1] == SYSTEM_CONTENT
    assert USER_CONTENT not in cmd
    opts = mock_run.call_args[1]["options"]
    assert opts.input_text == USER_CONTENT


async def test_subprocess_options_isolate_claude_runtime() -> None:
    """Claude subprocess should not inherit repo cwd or arbitrary env."""
    result = _make_subprocess_result(_make_envelope())
    mock_run = AsyncMock(return_value=result)
    adapter = _make_adapter()

    with (
        patch("jobfeed.adapters.llm.claude.run_with_retry", mock_run),
        patch.dict(
            "jobfeed.adapters.llm.claude.os.environ",
            {
                "ANTHROPIC_API_KEY": "test-key",
                "PATH": "/usr/bin",
                "USER": "tester",
                "JOBFEED_SECRET": "do-not-leak",
            },
            clear=True,
        ),
    ):
        await adapter.complete(_make_request())

    opts = mock_run.call_args[1]["options"]
    assert opts.start_new_session is True
    assert opts.cwd is not None
    assert "jobfeed-claude-" in opts.cwd
    # USER passes through (needed for macOS keychain OAuth); secrets do not.
    assert opts.env == {
        "ANTHROPIC_API_KEY": "test-key",
        "PATH": "/usr/bin",
        "USER": "tester",
    }


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


async def test_model_falls_back_to_configured_model_without_model_usage() -> None:
    """Response model falls back when Claude omits modelUsage."""
    envelope = _default_envelope()
    del envelope["modelUsage"]
    result = _make_subprocess_result(json.dumps(envelope))
    adapter = _make_adapter()

    with patch(
        "jobfeed.adapters.llm.claude.run_with_retry",
        AsyncMock(return_value=result),
    ):
        response = await adapter.complete(_make_request())

    assert response.model == MODEL


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


async def test_cache_creation_tokens_count_as_input_volume() -> None:
    """cache_creation_input_tokens should be validated and persisted in totals."""
    stdout = _make_envelope(
        cache_creation_input_tokens=CACHE_CREATION_TOKENS,
        cache_read_input_tokens=0,
    )
    result = _make_subprocess_result(stdout)
    adapter = _make_adapter()

    with patch(
        "jobfeed.adapters.llm.claude.run_with_retry",
        AsyncMock(return_value=result),
    ):
        response = await adapter.complete(_make_request())

    assert response.input_tokens == INPUT_TOKENS + CACHE_CREATION_TOKENS
    assert response.cached is True


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


@pytest.mark.parametrize(
    ("stdout", "error"),
    [
        (json.dumps(["not", "an", "object"]), "JSON envelope must be an object"),
        (_make_envelope(result=42), "field must be string: result"),
        (
            json.dumps(_default_envelope() | {"usage": "bad"}),
            "field must be object: usage",
        ),
        (_make_envelope(input_tokens="500"), "field must be integer: input_tokens"),
        (
            _make_envelope(cache_creation_input_tokens="500"),
            "field must be integer: cache_creation_input_tokens",
        ),
    ],
)
async def test_invalid_json_envelope_shape_raises_value_error(
    stdout: str,
    error: str,
) -> None:
    """Claude JSON envelope fields should be validated before persistence."""
    result = _make_subprocess_result(stdout)
    adapter = _make_adapter()

    with (
        patch(
            "jobfeed.adapters.llm.claude.run_with_retry",
            AsyncMock(return_value=result),
        ),
        pytest.raises(ValueError, match=error),
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
# Stdin prompt, detached session
# ---------------------------------------------------------------------------


async def test_stdin_and_detached_session() -> None:
    """SubprocessOptions passes user content on stdin and detaches process group."""
    result = _make_subprocess_result(_make_envelope())
    mock_run = AsyncMock(return_value=result)
    adapter = _make_adapter()

    with patch("jobfeed.adapters.llm.claude.run_with_retry", mock_run):
        await adapter.complete(_make_request())

    opts = mock_run.call_args[1]["options"]
    assert opts.input_text == USER_CONTENT
    assert opts.start_new_session is True
