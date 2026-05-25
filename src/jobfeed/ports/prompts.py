"""Service-facing prompt rendering port."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from jobfeed.domain.models import JobPosting, Message


@dataclass(frozen=True, kw_only=True)
class PromptBundle:
    """Rendered LLM request messages plus stable hashes."""

    messages: list[Message]
    prompt_hash: str
    resume_hash: str


@runtime_checkable
class PromptRenderer(Protocol):
    """Render stage prompts without exposing template implementation details."""

    def render_stage_a(self, *, resume_text: str, job: JobPosting) -> PromptBundle:
        """Render Stage A prompt bundle.

        Args:
            resume_text: Master resume text.
            job: Job posting to evaluate.

        Returns:
            PromptBundle with messages and hashes.
        """
        ...

    def render_stage_b(self, *, resume_text: str, job: JobPosting) -> PromptBundle:
        """Render Stage B prompt bundle.

        Args:
            resume_text: Master resume text.
            job: Job posting to evaluate.

        Returns:
            PromptBundle with messages and hashes.
        """
        ...


__all__ = ["PromptBundle", "PromptRenderer"]
