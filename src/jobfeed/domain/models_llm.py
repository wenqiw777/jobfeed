"""Adapter-neutral LLM data-transfer models for the domain layer.

Split out of ``models.py`` to keep that module within the file-length gate.
Re-exported from ``jobfeed.domain.models`` so existing import paths keep working.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal


@dataclass(kw_only=True)
class LLMUsage:
    """Token, cost, cache, and latency metrics for one LLM call."""

    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    cached: bool
    latency_ms: int
    timestamp: datetime
    job_id: str | None = None
    stage: Literal["a", "b", "evaluation"] | None = None
    run_id: str | None = None


@dataclass(kw_only=True)
class Message:
    """Adapter-neutral LLM chat message."""

    role: Literal["system", "user", "assistant"]
    content: str


@dataclass(kw_only=True)
class LLMRequest:
    """Adapter-neutral LLM completion request."""

    messages: list[Message]
    model: str
    temperature: float = 0.0
    max_tokens: int = 4096
    response_schema: dict[str, object] | None = None


@dataclass(kw_only=True)
class LLMResponse:
    """Adapter-neutral LLM completion response."""

    content: str
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float | None = None
    cached: bool = False
    latency_ms: int = 0


__all__ = ["LLMRequest", "LLMResponse", "LLMUsage", "Message"]
