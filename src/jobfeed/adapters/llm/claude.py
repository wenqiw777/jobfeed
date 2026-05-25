"""ClaudeCliLLM adapter — subprocess-based LLM client for Claude Code CLI.

Uses ``claude -p --output-format json`` mode for structured JSON output.
System prompt via ``--system-prompt``, user prompt as positional argument.
No price table needed — the CLI returns exact ``total_cost_usd``.
"""

from __future__ import annotations

import json

from jobfeed.adapters.llm._subprocess import (
    RetryOptions,
    SubprocessOptions,
    run_with_retry,
)
from jobfeed.domain.models import LLMRequest, LLMResponse
from jobfeed.observability import JobfeedLogger


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
        timeout_s: float = 210.0,
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
        cmd = self._build_command(request)
        opts = SubprocessOptions(timeout_s=self._timeout_s)
        retry = RetryOptions(max_retries=self._max_retries)

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
        user_content = request.messages[1].content

        return [
            "claude",
            "-p",
            "--no-session-persistence",
            "--tools",
            "",
            "--output-format",
            "json",
            "--model",
            self._model,
            "--system-prompt",
            system_content,
            user_content,
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

        content: str = envelope["result"]
        cost: float = envelope["total_cost_usd"]
        latency: int = envelope.get("duration_api_ms", 0)
        usage: dict[str, int] = envelope["usage"]

        cached_tokens = int(usage.get("cache_read_input_tokens", 0))

        return LLMResponse(
            content=content,
            model=self._model,
            input_tokens=int(usage["input_tokens"]),
            output_tokens=int(usage["output_tokens"]),
            cost_usd=cost,
            cached=cached_tokens > 0,
            latency_ms=latency,
        )


__all__ = ["ClaudeCliLLM"]
