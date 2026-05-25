"""CodexCliLLM adapter — subprocess-based LLM client for Codex CLI.

Uses ``codex exec --json`` mode for structured JSONL event stream.
Prompt passed via stdin with ``<system>`` wrapper.  TTY detached via
``start_new_session=True``.
"""

from __future__ import annotations

import asyncio
import json

from jobfeed.adapters.llm._pricing import ModelPricing, estimate_cost
from jobfeed.adapters.llm._subprocess import (
    RetryOptions,
    SubprocessOptions,
    run_with_retry,
)
from jobfeed.domain.models import LLMRequest, LLMResponse
from jobfeed.observability import JobfeedLogger

_SYSTEM_OPEN = "<system>"
_SYSTEM_CLOSE = "</system>"
_EVENT_ERROR = "error"
_EVENT_ITEM_COMPLETED = "item.completed"
_EVENT_TURN_COMPLETED = "turn.completed"
_ITEM_TYPE_AGENT_MESSAGE = "agent_message"
_RETRY_DELAY_S = 2.0


class CodexApiError(Exception):
    """Codex returned an error event in JSONL (exit code 0)."""


class CodexCliLLM:
    """LLMClient adapter for Codex CLI (``codex exec``).

    Uses ``--json`` mode for structured JSONL event stream.
    Prompt passed via stdin.  TTY detached via ``start_new_session``.
    """

    def __init__(
        self,
        *,
        model: str,
        timeout_s: float = 60.0,
        max_retries: int = 2,
        price_table: dict[str, ModelPricing],
        logger: JobfeedLogger,
    ) -> None:
        self._model = model
        self._timeout_s = timeout_s
        self._max_retries = max_retries
        self._price_table = price_table
        self._logger = logger

    async def complete(self, request: LLMRequest) -> LLMResponse:
        """Run a Codex CLI completion and return an adapter-neutral response.

        Args:
            request: Adapter-neutral completion request with messages.

        Returns:
            Parsed LLM response with content, usage, and cost.

        Raises:
            CodexApiError: When Codex returns an error event or no
                agent_message is found after all retries.
        """
        cmd = self._build_command()
        stdin = self._build_stdin(request)
        opts = SubprocessOptions(
            input_text=stdin,
            timeout_s=self._timeout_s,
            start_new_session=True,
        )
        retry = RetryOptions(max_retries=self._max_retries)

        for attempt in range(1 + self._max_retries):
            result = await run_with_retry(
                cmd,
                options=opts,
                retry=retry,
                logger=self._logger,
            )
            try:
                return self._parse_response(result.stdout, result.elapsed_ms)
            except CodexApiError:
                if attempt >= self._max_retries:
                    raise
                self._logger.warning(
                    "codex_api_error_retry",
                    attempt=attempt + 1,
                )
                await asyncio.sleep(_RETRY_DELAY_S)

        # Unreachable: loop always returns or raises.
        msg = "codex retry loop exited without returning"
        raise RuntimeError(msg)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _build_command(self) -> list[str]:
        """Build the ``codex exec`` command list."""
        return [
            "codex",
            "exec",
            "--json",
            "--ephemeral",
            "--ignore-user-config",
            "--ignore-rules",
            "--sandbox",
            "read-only",
            "-m",
            self._model,
            "-",
        ]

    @staticmethod
    def _build_stdin(request: LLMRequest) -> str:
        """Format request messages as stdin for Codex."""
        system = request.messages[0].content
        user = request.messages[1].content
        return f"{_SYSTEM_OPEN}\n{system}\n{_SYSTEM_CLOSE}\n\n{user}"

    def _parse_response(self, stdout: str, elapsed_ms: int) -> LLMResponse:
        """Parse JSONL event stream into an ``LLMResponse``.

        Args:
            stdout: Raw JSONL output from Codex.
            elapsed_ms: Subprocess wall-clock time.

        Returns:
            Parsed response with content, token counts, and cost.

        Raises:
            CodexApiError: On error events or missing agent message.
        """
        agent_text: str | None = None
        usage: dict[str, int] = {}

        for line in stdout.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            try:
                event = json.loads(stripped)
            except json.JSONDecodeError:
                self._logger.warning("skipping malformed JSONL line")
                continue
            self._handle_event(event, agent_text_ref := [agent_text], usage)
            agent_text = agent_text_ref[0]

        if agent_text is None:
            raise CodexApiError("no agent_message in response")

        return self._build_response(agent_text, usage, elapsed_ms)

    def _handle_event(
        self,
        event: dict[str, object],
        agent_text_ref: list[str | None],
        usage: dict[str, int],
    ) -> None:
        """Dispatch a single JSONL event.

        Args:
            event: Parsed JSON event dict.
            agent_text_ref: Mutable single-element list for agent text.
            usage: Mutable dict accumulating usage tokens.

        Raises:
            CodexApiError: When the event is an error event.
        """
        event_type = event.get("type")
        if event_type == _EVENT_ERROR:
            raise CodexApiError(str(event.get("message", "unknown error")))
        if event_type == _EVENT_ITEM_COMPLETED:
            item = event.get("item", {})
            if isinstance(item, dict) and item.get("type") == _ITEM_TYPE_AGENT_MESSAGE:
                agent_text_ref[0] = str(item.get("text", ""))
        elif event_type == _EVENT_TURN_COMPLETED:
            raw_usage = event.get("usage")
            if isinstance(raw_usage, dict):
                usage.update(raw_usage)

    def _build_response(
        self,
        content: str,
        usage: dict[str, int],
        elapsed_ms: int,
    ) -> LLMResponse:
        """Construct an ``LLMResponse`` from parsed event data."""
        input_tokens = int(usage.get("input_tokens", 0))
        output_tokens = int(usage.get("output_tokens", 0))
        cached_input = int(usage.get("cached_input_tokens", 0))
        reasoning_output = int(usage.get("reasoning_output_tokens", 0))

        cost = estimate_cost(
            self._model,
            input_tokens,
            output_tokens,
            cached_input_tokens=cached_input,
            reasoning_output_tokens=reasoning_output,
            price_table=self._price_table,
        )

        return LLMResponse(
            content=content,
            model=self._model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost,
            cached=cached_input > 0,
            latency_ms=elapsed_ms,
        )


__all__ = ["CodexApiError", "CodexCliLLM"]
