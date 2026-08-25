"""Prompt rendering tests for the single-pass objective evaluator."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from jobfeed.adapters.llm._prompts import JinjaPromptRenderer, render_unified_prompt
from jobfeed.domain.models import JobPosting

TEMPLATES_DIR = Path(__file__).parents[2] / "src" / "jobfeed" / "templates"
SHA256_HEX_LENGTH = 64


def _job() -> JobPosting:
    return JobPosting(
        platform="test",
        canonical_id="job-1",
        url="https://example.com/job-1",
        title="Backend Engineer",
        company="Example",
        location="Remote",
        discovered_at=datetime(2026, 8, 25, tzinfo=UTC),
        jd_text="Python required. Payments experience preferred.",
    )


def test_render_unified_prompt_returns_stable_messages_and_hashes() -> None:
    """Unified rendering should use one objective system prompt and user payload."""
    messages, prompt_hash, resume_hash = render_unified_prompt(
        "Built Python services.",
        _job(),
        TEMPLATES_DIR,
    )

    assert [message.role for message in messages] == ["system", "user"]
    system = messages[0].content
    assert '"match_tier"' in system
    assert "strong_match" in system
    assert "possible_match" in system
    assert "weak_match" in system
    assert "ineligible" in system
    assert "Do not output match_score" in system
    assert "ATS visibility is a separate diagnostic" in system
    assert "company growth" not in system.lower()
    assert len(prompt_hash) == SHA256_HEX_LENGTH
    assert len(resume_hash) == SHA256_HEX_LENGTH


def test_renderer_exposes_render_unified_without_personal_preamble() -> None:
    """Legacy preference preambles must not leak into objective evaluation."""
    renderer = JinjaPromptRenderer(
        TEMPLATES_DIR,
        preamble_path=Path("/definitely/not/used/by/unified.md"),
    )

    bundle = renderer.render_unified(resume_text="Built Python.", job=_job())

    assert bundle.messages[0].content.startswith("# Unified objective evaluation")
    assert bundle.prompt_hash
    assert bundle.resume_hash
