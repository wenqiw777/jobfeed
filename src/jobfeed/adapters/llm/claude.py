"""ClaudeCliLLM adapter — subprocess-based LLM client for Claude Code CLI.

Uses ``claude -p --output-format json`` mode for structured JSON output.
System prompt via ``--system-prompt``, user prompt via stdin.
No price table needed — the CLI returns exact ``total_cost_usd``.
"""

from __future__ import annotations

import json
import os
import tempfile

from jobfeed.adapters.llm._subprocess import (
    RetryOptions,
    SubprocessOptions,
    run_with_retry,
)
from jobfeed.domain.models import LLMRequest, LLMResponse
from jobfeed.observability import JobfeedLogger

_CLAUDE_ENV_KEYS = frozenset(
    {
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "ANTHROPIC_BASE_URL",
        "HOME",
        "HTTPS_PROXY",
        "HTTP_PROXY",
        "LOGNAME",
        "NO_PROXY",
        "PATH",
        "SSL_CERT_DIR",
        "SSL_CERT_FILE",
        "USER",
    }
)


class ClaudeCliLLM:
    """LLMClient adapter for Claude Code CLI (``claude -p``).

    Uses ``--output-format json`` for a structured JSON envelope containing
    the result text, token usage, cost, and latency.  No price estimation
    needed — the CLI reports exact ``total_cost_usd``.
    """

    def __init__(
        self,
        *,
        model: str,
        timeout_s: float | None = 210.0,
        max_retries: int = 2,
        logger: JobfeedLogger,
    ) -> None:
        self._model = model
        self._timeout_s = timeout_s
        self._max_retries = max_retries
        self._logger = logger

    async def complete(self, request: LLMRequest) -> LLMResponse:
        """Run a Claude CLI completion and return an adapter-neutral response.

        Args:
            request: Adapter-neutral completion request with messages.

        Returns:
            Parsed LLM response with content, usage, and exact cost.

        Raises:
            ValueError: When the CLI output is not valid JSON.
        """
        retry = RetryOptions(max_retries=self._max_retries)

        with tempfile.TemporaryDirectory(prefix="jobfeed-claude-") as workdir:
            cmd = self._build_command(request)
            opts = SubprocessOptions(
                input_text=request.messages[1].content,
                timeout_s=self._timeout_s,
                start_new_session=True,
                cwd=workdir,
                env=_claude_env(),
            )

            result = await run_with_retry(
                cmd,
                options=opts,
                retry=retry,
                logger=self._logger,
            )

        return self._parse_envelope(result.stdout)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _build_command(self, request: LLMRequest) -> list[str]:
        """Build the ``claude -p`` command list."""
        system_content = request.messages[0].content

        return [
            "claude",
            "-p",
            # Skip user/project/local settings (hooks, MCP, auto-memory) for an
            # isolated evaluation call. Unlike --bare, this still allows OAuth /
            # keychain auth, so subscription logins work (not just API keys).
            "--setting-sources",
            "",
            "--no-session-persistence",
            "--tools",
            "",
            "--output-format",
            "json",
            "--input-format",
            "text",
            "--model",
            self._model,
            "--system-prompt",
            system_content,
        ]

    def _parse_envelope(self, stdout: str) -> LLMResponse:
        """Parse JSON envelope from Claude CLI output.

        Args:
            stdout: Raw JSON output from the CLI.

        Returns:
            Parsed response with content, token counts, exact cost, and latency.

        Raises:
            ValueError: When stdout is not valid JSON or missing required keys.
        """
        try:
            envelope = json.loads(stdout)
        except json.JSONDecodeError as exc:
            msg = f"Claude CLI returned malformed JSON: {exc}"
            raise ValueError(msg) from exc
        if not isinstance(envelope, dict):
            raise ValueError("Claude CLI JSON envelope must be an object")

        content = _require_str(envelope, "result")
        cost = _require_number(envelope, "total_cost_usd")
        latency = _optional_int(envelope, "duration_api_ms")
        usage = _require_dict(envelope, "usage")
        model = self._resolve_model(envelope)

        cache_creation_tokens = _optional_int(usage, "cache_creation_input_tokens")
        cache_read_tokens = _optional_int(usage, "cache_read_input_tokens")
        total_input_tokens = (
            _require_int(usage, "input_tokens")
            + cache_creation_tokens
            + cache_read_tokens
        )

        return LLMResponse(
            content=content,
            model=model,
            input_tokens=total_input_tokens,
            output_tokens=_require_int(usage, "output_tokens"),
            cost_usd=cost,
            cached=(cache_creation_tokens + cache_read_tokens) > 0,
            latency_ms=latency,
        )

    def _resolve_model(self, envelope: dict[str, object]) -> str:
        """Return the concrete charged model when Claude reports it."""
        model_usage = envelope.get("modelUsage")
        if not isinstance(model_usage, dict) or not model_usage:
            return self._model
        model_names = sorted(str(name) for name in model_usage)
        if len(model_names) == 1:
            return model_names[0]
        return ",".join(model_names)


def _claude_env() -> dict[str, str]:
    return {key: value for key, value in os.environ.items() if key in _CLAUDE_ENV_KEYS}


def _require_str(data: dict[str, object], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str):
        raise ValueError(f"Claude CLI JSON field must be string: {key}")
    return value


def _require_dict(data: dict[str, object], key: str) -> dict[str, object]:
    value = data.get(key)
    if not isinstance(value, dict):
        raise ValueError(f"Claude CLI JSON field must be object: {key}")
    return value


def _require_number(data: dict[str, object], key: str) -> float:
    value = data.get(key)
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"Claude CLI JSON field must be numeric: {key}")
    return float(value)


def _require_int(data: dict[str, object], key: str) -> int:
    value = data.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"Claude CLI JSON field must be integer: {key}")
    return value


def _optional_int(data: dict[str, object], key: str) -> int:
    value = data.get(key, 0)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"Claude CLI JSON field must be integer: {key}")
    return value


__all__ = ["ClaudeCliLLM"]
