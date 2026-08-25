"""Jinja2-dependent prompt rendering adapter for job evaluation."""

from __future__ import annotations

import json
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from jobfeed.domain.candidate_facts import CandidateScoringProfile
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
    stage_a_score: int | None = None,
) -> tuple[list[Message], str, str]:
    """Render the Stage B prompt via Jinja2.

    Args:
        resume_text: Master resume text.
        job: Job posting to evaluate.
        templates_dir: Directory containing Jinja2 templates.
        preamble_path: Optional personal preamble file.
        stage_a_score: Optional Stage A rough score for calibration.

    Returns:
        Tuple of (messages, prompt_hash, resume_hash).
    """
    system_prompt = render_system_prompt(
        "stage_b_prompt.md",
        templates_dir,
        preamble_path=preamble_path,
        blocks=DEFAULT_BLOCKS,
    )
    user_msg = render_user_message(resume_text, job, stage_a_score=stage_a_score)
    messages = [
        Message(role="system", content=system_prompt),
        Message(role="user", content=user_msg),
    ]
    p_hash = compute_prompt_hash(system_prompt)
    r_hash = compute_resume_hash(resume_text)
    return messages, p_hash, r_hash


def render_unified_prompt(
    resume_text: str,
    job: JobPosting,
    templates_dir: Path,
) -> tuple[list[Message], str, str]:
    """Render the single-pass objective evaluation prompt.

    Legacy personal preambles are deliberately excluded because they contain
    preference and action-ranking guidance outside this evaluator's contract.

    Args:
        resume_text: Master resume text used as candidate evidence.
        job: Job posting to evaluate.
        templates_dir: Directory containing Jinja2 templates.

    Returns:
        Tuple of messages, objective prompt hash, and resume hash.
    """
    system_prompt = render_system_prompt(
        "unified_evaluation_prompt.md",
        templates_dir,
    )
    profile = CandidateScoringProfile.from_resume(resume_text)
    candidate_facts = json.dumps(
        {
            "actual_experience_level": profile.actual_level,
            "non_intern_professional_months": profile.professional_months,
            "internship_months": profile.internship_months,
            "degree_level": profile.degree_level,
            "degree_status": profile.degree_status,
            "graduation_month": profile.graduation_month,
        },
        sort_keys=True,
    )
    user_message = (
        "The following timeline and education facts were computed by code from "
        "the resume. Use them for level, duration, degree, and graduation checks; "
        "cite the original resume text in output evidence.\n"
        f"<BEGIN_CANDIDATE_FACTS>\n{candidate_facts}\n<END_CANDIDATE_FACTS>\n\n"
        f"{render_user_message(resume_text, job)}"
    )
    messages = [
        Message(role="system", content=system_prompt),
        Message(role="user", content=user_message),
    ]
    return (
        messages,
        compute_prompt_hash(system_prompt),
        compute_resume_hash(resume_text),
    )


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

    def render_stage_b(
        self,
        *,
        resume_text: str,
        job: JobPosting,
        stage_a_score: int | None = None,
    ) -> PromptBundle:
        """Render Stage B prompt bundle.

        Args:
            resume_text: Master resume text.
            job: Job posting to evaluate.

        Returns:
            PromptBundle with messages and hashes.
        """
        messages, prompt_hash, resume_hash = render_stage_b_prompt(
            resume_text, job, self._templates_dir, self._preamble_path, stage_a_score
        )
        return PromptBundle(
            messages=messages,
            prompt_hash=prompt_hash,
            resume_hash=resume_hash,
        )

    def render_unified(self, *, resume_text: str, job: JobPosting) -> PromptBundle:
        """Render the single-pass objective evaluation bundle.

        Args:
            resume_text: Master resume text used as candidate evidence.
            job: Job posting to evaluate.

        Returns:
            Prompt bundle with stable objective prompt and resume hashes.
        """
        messages, prompt_hash, resume_hash = render_unified_prompt(
            resume_text,
            job,
            self._templates_dir,
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
    "render_unified_prompt",
]
