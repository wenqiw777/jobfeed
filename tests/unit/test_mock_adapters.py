"""Unit tests for deterministic mock adapters."""

from __future__ import annotations

from jobfeed.adapters.llm.mock import MockLLM
from jobfeed.adapters.sources.mock import MOCK_JD_TEXT, MockSource
from jobfeed.domain.models import LLMRequest, QualityBand
from jobfeed.domain.quality import assess_quality
from jobfeed.domain.scoring import parse_stage_a_response, parse_stage_b_response
from jobfeed.ports.llm import LLMClient
from jobfeed.ports.source import SimpleSource

MOCK_JOB_COUNT = 5
MOCK_STAGE_A_SCORE = 75
MOCK_STAGE_B_SCORE = 72
MOCK_TOKEN_COUNT = 100
MIN_GOOD_JD_LENGTH = 500
PROMPT_HASH = "prompt"
RESUME_HASH = "resume"


async def test_mock_llm_stage_a_response_matches_parser_contract() -> None:
    """MockLLM Stage A output should parse through the canonical parser."""
    llm = MockLLM()
    request = LLMRequest(messages=[], model="mock/stage-a")

    assert isinstance(llm, LLMClient)
    response = await llm.complete(request)
    result = parse_stage_a_response(
        response.content,
        model=response.model,
        prompt_hash=PROMPT_HASH,
        resume_hash=RESUME_HASH,
        cost_usd=response.cost_usd or 0.0,
    )

    assert result.score == MOCK_STAGE_A_SCORE
    assert result.one_line == "Mock evaluation"
    assert response.input_tokens + response.output_tokens == MOCK_TOKEN_COUNT


async def test_mock_llm_stage_b_response_matches_parser_contract() -> None:
    """MockLLM Stage B output should parse through the canonical parser."""
    llm = MockLLM()
    request = LLMRequest(messages=[], model="mock/stage-b")

    response = await llm.complete(request)
    result = parse_stage_b_response(
        response.content,
        model=response.model,
        prompt_hash=PROMPT_HASH,
        resume_hash=RESUME_HASH,
        cost_usd=response.cost_usd or 0.0,
    )

    assert result.verdict.value == "consider"
    assert result.fit_analysis.score == MOCK_STAGE_B_SCORE
    assert result.fit_analysis.strengths[0].evidence == (
        "Resume shows production Python work."
    )
    assert result.resume_hooks == ["Mention the local-first job pipeline."]
    assert result.raw_blocks is not None


async def test_mock_source_returns_configurable_mock_jobs() -> None:
    """MockSource should return stable platform identities and count."""
    source = MockSource()

    assert isinstance(source, SimpleSource)
    jobs = await source.fetch_jobs({"count": MOCK_JOB_COUNT})

    assert len(jobs) == MOCK_JOB_COUNT
    assert {job.platform for job in jobs} == {"mock"}
    assert jobs[0].canonical_id == "mock-1"


async def test_mock_source_jd_quality_is_deterministic() -> None:
    """MockSource should return long JD text with deterministic quality."""
    source = MockSource()
    jobs = await source.fetch_jobs({"count": 1})

    assert len(MOCK_JD_TEXT) > MIN_GOOD_JD_LENGTH
    assert jobs[0].jd_text == MOCK_JD_TEXT
    assert assess_quality(jobs[0].jd_text) in {QualityBand.GOOD, QualityBand.FULL}
