"""Jinja2-dependent prompt rendering adapter for Stage A and Stage B."""

from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from jobfeed.domain.models import JobPosting, Message
from jobfeed.domain.scoring import (
    compute_prompt_hash,
    compute_resume_hash,
    render_user_message,
)
from jobfeed.ports.prompts import PromptBundle

DEFAULT_BLOCKS = ("verdict", "jd_summary", "fit_analysis", "resume_hooks")


def render_system_prompt(
    template_name: str,
    templates_dir: Path,
    *,
    preamble_path: Path | None = None,
    blocks: tuple[str, ...] | None = None,
) -> str:
    """Render a Jinja2 system prompt template.

    Args:
        template_name: Template filename within templates_dir.
        templates_dir: Directory containing Jinja2 templates.
        preamble_path: Optional personal preamble file to append.
        blocks: Block names passed to the template context.

    Returns:
        Rendered system prompt string.
    """
    env = Environment(
        loader=FileSystemLoader(str(templates_dir)),
        keep_trailing_newline=True,
    )
    template = env.get_template(template_name)
    context: dict[str, object] = {}
    if blocks is not None:
        context["blocks"] = blocks
    rendered = template.render(context)
    if preamble_path is not None and preamble_path.is_file():
        preamble_content = preamble_path.read_text(encoding="utf-8")
        rendered = rendered + "\n\n" + preamble_content
    return rendered


def render_stage_a_prompt(
    resume_text: str,
    job: JobPosting,
    templates_dir: Path,
    preamble_path: Path | None,
) -> tuple[list[Message], str, str]:
    """Render the Stage A prompt via Jinja2.

    Args:
        resume_text: Master resume text.
        job: Job posting to evaluate.
        templates_dir: Directory containing Jinja2 templates.
        preamble_path: Optional personal preamble file.

    Returns:
        Tuple of (messages, prompt_hash, resume_hash).
    """
    system_prompt = render_system_prompt(
        "stage_a_prompt.md",
        templates_dir,
        preamble_path=preamble_path,
    )
    user_msg = render_user_message(resume_text, job)
    messages = [
        Message(role="system", content=system_prompt),
        Message(role="user", content=user_msg),
    ]
    p_hash = compute_prompt_hash(system_prompt)
    r_hash = compute_resume_hash(resume_text)
    return messages, p_hash, r_hash


def render_stage_b_prompt(
    resume_text: str,
    job: JobPosting,
    templates_dir: Path,
    preamble_path: Path | None,
) -> tuple[list[Message], str, str]:
    """Render the Stage B prompt via Jinja2.

    Args:
        resume_text: Master resume text.
        job: Job posting to evaluate.
        templates_dir: Directory containing Jinja2 templates.
        preamble_path: Optional personal preamble file.

    Returns:
        Tuple of (messages, prompt_hash, resume_hash).
    """
    system_prompt = render_system_prompt(
        "stage_b_prompt.md",
        templates_dir,
        preamble_path=preamble_path,
        blocks=DEFAULT_BLOCKS,
    )
    user_msg = render_user_message(resume_text, job)
    messages = [
        Message(role="system", content=system_prompt),
        Message(role="user", content=user_msg),
    ]
    p_hash = compute_prompt_hash(system_prompt)
    r_hash = compute_resume_hash(resume_text)
    return messages, p_hash, r_hash


class JinjaPromptRenderer:
    """Concrete PromptRenderer using Jinja2 templates.

    Args:
        templates_dir: Directory containing Jinja2 templates.
        preamble_path: Optional personal preamble file.
    """

    def __init__(
        self,
        templates_dir: Path,
        preamble_path: Path | None = None,
    ) -> None:
        self._templates_dir = templates_dir
        self._preamble_path = preamble_path

    def render_stage_a(self, *, resume_text: str, job: JobPosting) -> PromptBundle:
        """Render Stage A prompt bundle.

        Args:
            resume_text: Master resume text.
            job: Job posting to evaluate.

        Returns:
            PromptBundle with messages and hashes.
        """
        messages, prompt_hash, resume_hash = render_stage_a_prompt(
            resume_text, job, self._templates_dir, self._preamble_path
        )
        return PromptBundle(
            messages=messages,
            prompt_hash=prompt_hash,
            resume_hash=resume_hash,
        )

    def render_stage_b(self, *, resume_text: str, job: JobPosting) -> PromptBundle:
        """Render Stage B prompt bundle.

        Args:
            resume_text: Master resume text.
            job: Job posting to evaluate.

        Returns:
            PromptBundle with messages and hashes.
        """
        messages, prompt_hash, resume_hash = render_stage_b_prompt(
            resume_text, job, self._templates_dir, self._preamble_path
        )
        return PromptBundle(
            messages=messages,
            prompt_hash=prompt_hash,
            resume_hash=resume_hash,
        )


__all__ = [
    "JinjaPromptRenderer",
    "render_stage_a_prompt",
    "render_stage_b_prompt",
    "render_system_prompt",
]
