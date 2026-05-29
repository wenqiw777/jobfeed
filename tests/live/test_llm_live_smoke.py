"""Live smoke tests that make real LLM calls via Codex CLI and Claude CLI.

These tests are never run in CI and are excluded from the default test suite.
Run manually with: pytest -m live -o "addopts="

They exercise the real LLM CLI adapters against live model endpoints to verify
that prompt rendering, subprocess invocation, response parsing, and scoring all
work end-to-end with actual model output.
"""

from __future__ import annotations

import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest
import structlog

from jobfeed.adapters.llm._pricing import load_price_table
from jobfeed.adapters.llm._prompts import render_stage_a_prompt, render_stage_b_prompt
from jobfeed.adapters.llm._subprocess import SubprocessError
from jobfeed.adapters.llm.claude import ClaudeCliLLM
from jobfeed.adapters.llm.codex import CodexApiError, CodexCliLLM
from jobfeed.domain.models import JobPosting, LLMRequest, LLMResponse, Verdict
from jobfeed.domain.scoring_parse import parse_stage_a_response, parse_stage_b_response
from jobfeed.ports.llm import LLMClient

pytestmark = pytest.mark.live

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TEMPLATES_DIR = Path(__file__).resolve().parents[2] / "src" / "jobfeed" / "templates"
RESUME_TEXT = (
    "Experienced Python backend engineer with 5 years building REST APIs, "
    "async services, and data pipelines."
)
STAGE_A_MIN_SCORE = 0
STAGE_A_MAX_SCORE = 100
STAGE_A_REFERENCE_SCORE = 75
CODEX_STAGE_A_MODEL = "gpt-5.4-mini"
CODEX_STAGE_B_MODEL = "gpt-5.5"
CLAUDE_STAGE_A_MODEL = "claude-haiku-4-5"
CLAUDE_STAGE_B_MODEL = "claude-sonnet-4-6"
AUTH_FAILURE_MARKERS = (
    "api key",
    "auth",
    "authentication",
    "login",
    "not logged in",
    "unauthorized",
    "401",
    "403",
    "anthropic_api_key",
    "openai_api_key",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_test_job() -> JobPosting:
    """Build a realistic test job posting for LLM evaluation."""
    return JobPosting(
        platform="test",
        canonical_id="live-1",
        url="https://example.com/jobs/1",
        title="Senior Backend Engineer",
        company="TestCo",
        location="Remote",
        discovered_at=datetime.now(UTC),
        jd_text=(
            "We are looking for a Senior Backend Engineer with Python experience. "
            "Requirements: 3+ years Python, REST API design, PostgreSQL, async "
            "programming. Nice to have: Kubernetes, CI/CD pipelines. Fully remote "
            "position."
        ),
    )


def _skip_if_missing(binary: str) -> None:
    """Skip the current test when a required CLI binary is not on PATH."""
    if shutil.which(binary) is None:
        pytest.skip(f"{binary} not found on PATH")


async def _complete_or_skip_auth(
    llm: LLMClient,
    request: LLMRequest,
) -> LLMResponse:
    """Run a live completion, skipping when local CLI auth is unavailable."""
    try:
        return await llm.complete(request)
    except (CodexApiError, SubprocessError) as exc:
        message = str(exc).lower()
        if any(marker in message for marker in AUTH_FAILURE_MARKERS):
            pytest.skip(f"LLM CLI authentication unavailable: {exc}")
        raise


# ===========================================================================
# Live smoke tests
# ===========================================================================


class TestLLMLiveSmoke:
    """Smoke tests that invoke real LLM CLI backends."""

    # ---- Codex CLI --------------------------------------------------------

    async def test_codex_stage_a(self) -> None:
        """Run Stage A scoring through Codex CLI and verify parseable result."""
        _skip_if_missing("codex")
        price_table = load_price_table()
        logger = structlog.get_logger()
        llm = CodexCliLLM(
            model=CODEX_STAGE_A_MODEL,
            timeout_s=60,
            price_table=price_table,
            logger=logger,
        )
        job = _make_test_job()
        messages, phash, rhash = render_stage_a_prompt(
            RESUME_TEXT, job, TEMPLATES_DIR, None
        )
        request = LLMRequest(messages=messages, model=CODEX_STAGE_A_MODEL)
        response = await _complete_or_skip_auth(llm, request)
        result = parse_stage_a_response(
            response.content,
            model=CODEX_STAGE_A_MODEL,
            prompt_hash=phash,
            resume_hash=rhash,
        )
        assert STAGE_A_MIN_SCORE <= result.score <= STAGE_A_MAX_SCORE

    async def test_codex_stage_b(self) -> None:
        """Run Stage B evaluation through Codex CLI and verify parseable result."""
        _skip_if_missing("codex")
        price_table = load_price_table()
        logger = structlog.get_logger()
        llm = CodexCliLLM(
            model=CODEX_STAGE_B_MODEL,
            timeout_s=90,
            price_table=price_table,
            logger=logger,
        )
        job = _make_test_job()
        messages, phash, rhash = render_stage_b_prompt(
            RESUME_TEXT, job, TEMPLATES_DIR, None, STAGE_A_REFERENCE_SCORE
        )
        request = LLMRequest(messages=messages, model=CODEX_STAGE_B_MODEL)
        response = await _complete_or_skip_auth(llm, request)
        result = parse_stage_b_response(
            response.content,
            model=CODEX_STAGE_B_MODEL,
            prompt_hash=phash,
            resume_hash=rhash,
        )
        assert result.verdict in (Verdict.APPLY, Verdict.CONSIDER, Verdict.SKIP)
        assert STAGE_A_MIN_SCORE <= result.fit_analysis.score <= STAGE_A_MAX_SCORE

    # ---- Claude CLI -------------------------------------------------------

    async def test_claude_stage_a(self) -> None:
        """Run Stage A scoring through Claude CLI and verify parseable result."""
        _skip_if_missing("claude")
        logger = structlog.get_logger()
        llm = ClaudeCliLLM(
            model=CLAUDE_STAGE_A_MODEL,
            timeout_s=210,
            logger=logger,
        )
        job = _make_test_job()
        messages, phash, rhash = render_stage_a_prompt(
            RESUME_TEXT, job, TEMPLATES_DIR, None
        )
        request = LLMRequest(messages=messages, model=CLAUDE_STAGE_A_MODEL)
        response = await _complete_or_skip_auth(llm, request)
        result = parse_stage_a_response(
            response.content,
            model=CLAUDE_STAGE_A_MODEL,
            prompt_hash=phash,
            resume_hash=rhash,
        )
        assert STAGE_A_MIN_SCORE <= result.score <= STAGE_A_MAX_SCORE

    async def test_claude_stage_b(self) -> None:
        """Run Stage B evaluation through Claude CLI and verify parseable result."""
        _skip_if_missing("claude")
        logger = structlog.get_logger()
        llm = ClaudeCliLLM(
            model=CLAUDE_STAGE_B_MODEL,
            timeout_s=210,
            logger=logger,
        )
        job = _make_test_job()
        messages, phash, rhash = render_stage_b_prompt(
            RESUME_TEXT, job, TEMPLATES_DIR, None, STAGE_A_REFERENCE_SCORE
        )
        request = LLMRequest(messages=messages, model=CLAUDE_STAGE_B_MODEL)
        response = await _complete_or_skip_auth(llm, request)
        result = parse_stage_b_response(
            response.content,
            model=CLAUDE_STAGE_B_MODEL,
            prompt_hash=phash,
            resume_hash=rhash,
        )
        assert result.verdict in (Verdict.APPLY, Verdict.CONSIDER, Verdict.SKIP)
        assert STAGE_A_MIN_SCORE <= result.fit_analysis.score <= STAGE_A_MAX_SCORE
