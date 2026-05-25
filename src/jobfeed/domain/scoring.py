"""Pure scoring helpers for prompts and LLM response normalization.

Public API: re-exports parse functions from ``scoring_parse.py`` and
provides stdlib-only prompt helpers. Split across two files to stay
within the 300-line gate.
"""

from __future__ import annotations

import hashlib

from jobfeed.domain.models import (
    JobPosting,
    Message,
)
from jobfeed.domain.scoring_parse import (
    parse_stage_a_response,
    parse_stage_b_response,
)

STAGE_A_SYSTEM_PROMPT = "You are a precise job fit scoring assistant."
STAGE_B_SYSTEM_PROMPT = "You are a precise job application review assistant."
MAX_STAGE_RETRIES = 3

STAGE_A_JSON_CONTRACT = (
    'Return only JSON: {"score": 0, "one_line": "short reason", '
    '"timing_eligible": "eligible|maybe|ineligible"}. '
    "score must be an integer from 0 to 100."
)
STAGE_B_JSON_CONTRACT = (
    "Return only JSON with block_a.verdict, block_b.summary, "
    "block_c.score_0_100, block_c.strong_match[], block_c.gaps[], "
    "and block_e.hooks[]. verdict must be apply, consider, or skip; "
    "gap severity must be critical, major, or minor."
)


# ------------------------------------------------------------------
# Stdlib prompt helpers (used by adapters/llm/_prompts.py)
# ------------------------------------------------------------------


def render_user_message(resume_text: str, job: JobPosting) -> str:
    """Format the user message for LLM evaluation.

    Args:
        resume_text: Master resume Markdown.
        job: Job posting to evaluate.

    Returns:
        Formatted user message string.
    """
    return (
        f"# Master Resume\n\n{resume_text}\n\n"
        f"---\n\n"
        f"# Job Posting\n\n"
        f"**Title:** {job.title}\n"
        f"**Company:** {job.company}\n"
        f"**Location:** {job.location}\n"
        f"**Platform:** {job.platform}\n\n"
        f"## JD Text\n\n{job.jd_text or ''}"
    )


def compute_prompt_hash(system_prompt: str) -> str:
    """Compute a stable SHA-256 hash of the system prompt.

    Args:
        system_prompt: Full rendered system prompt text.

    Returns:
        Hex digest of the prompt hash.
    """
    return hashlib.sha256(system_prompt.encode()).hexdigest()


def compute_resume_hash(resume_text: str) -> str:
    """Compute a stable SHA-256 hash of the resume text.

    Args:
        resume_text: Full resume Markdown text.

    Returns:
        Hex digest of the resume hash.
    """
    return hashlib.sha256(resume_text.encode()).hexdigest()


# ------------------------------------------------------------------
# Phase 0 skeleton renderers (kept until Task 8 swaps the service)
# ------------------------------------------------------------------


def render_stage_a_prompt(
    jd_text: str,
    resume_md: str,
    rubric_md: str,
) -> list[Message]:
    """Render the Phase 0 Stage A scoring prompt as adapter-neutral messages.

    Args:
        jd_text: Job description text to score.
        resume_md: Resume Markdown used as fit evidence.
        rubric_md: Scoring rubric Markdown.

    Returns:
        Chat messages ready for an LLM adapter.
    """
    user_content = (
        "Score this job against the resume and rubric.\n\n"
        f"## Job Description\n{jd_text}\n\n"
        f"## Resume\n{resume_md}\n\n"
        f"## Rubric\n{rubric_md}\n\n"
        f"{STAGE_A_JSON_CONTRACT}"
    )
    return [
        Message(role="system", content=STAGE_A_SYSTEM_PROMPT),
        Message(role="user", content=user_content),
    ]


def render_stage_b_prompt(
    jd_text: str,
    resume_md: str,
    rubric_md: str,
) -> list[Message]:
    """Render the Phase 0 Stage B detailed review prompt.

    Args:
        jd_text: Job description text to review.
        resume_md: Resume Markdown used as evidence.
        rubric_md: Detailed review rubric Markdown.

    Returns:
        Chat messages ready for an LLM adapter.
    """
    user_content = (
        "Review this job for application readiness.\n\n"
        f"## Job Description\n{jd_text}\n\n"
        f"## Resume\n{resume_md}\n\n"
        f"## Rubric\n{rubric_md}\n\n"
        f"{STAGE_B_JSON_CONTRACT}"
    )
    return [
        Message(role="system", content=STAGE_B_SYSTEM_PROMPT),
        Message(role="user", content=user_content),
    ]


__all__ = [
    "MAX_STAGE_RETRIES",
    "compute_prompt_hash",
    "compute_resume_hash",
    "parse_stage_a_response",
    "parse_stage_b_response",
    "render_stage_a_prompt",
    "render_stage_b_prompt",
    "render_user_message",
]
