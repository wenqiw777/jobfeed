"""PostgreSQL golden contracts for Stage B JSON persistence and corruption."""

from __future__ import annotations

import json

import asyncpg  # type: ignore[import-untyped]
import pytest

from jobfeed.adapters.store.postgres import PostgresStore, _as_json_obj
from jobfeed.domain.models import QualityBand, StageAResult
from jobfeed.domain.scoring import parse_stage_b_response
from tests.support.factories import make_job

pytestmark = pytest.mark.postgres


def _stage_a() -> StageAResult:
    return StageAResult(
        score=80,
        one_line="Fit",
        timing_eligible="eligible",
        model="mock/stage-a",
        prompt_hash="stage-a-prompt",
        resume_hash="resume-a",
        cost_usd=0.01,
    )


def _raw_blocks() -> dict[str, object]:
    return {
        "resume_hooks": {
            "supporting": ["分布式系统"],
            "lead_with": "Python",
            "avoid_mentioning": [],
        },
        "fit_analysis": {
            "strong_match": [
                {
                    "evidence_from_resume": "Built services",
                    "requirement": "Python",
                }
            ],
            "score_0_100": 82,
            "gaps": [],
        },
        "jd_summary": {
            "nice_to_haves": ["Kubernetes"],
            "role_in_3_lines": "后端工程师",
            "red_flags_in_jd": [],
            "must_haves": ["Python"],
        },
        "verdict": {
            "one_line": "值得申请",
            "recommendation": "consider",
            "confidence": 3,
        },
    }


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode()


async def _json_text_columns(
    store: PostgresStore,
    job_id: str,
) -> dict[str, str]:
    pool = store._get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT stage_b_verdict_json::text AS verdict,
                      stage_b_summary_json::text AS summary,
                      stage_b_fit_json::text AS fit,
                      stage_b_hooks_json::text AS hooks
                 FROM evaluations
                WHERE job_id = $1""",
            int(job_id),
        )
    assert row is not None
    return dict(row)


async def test_stage_b_json_roundtrips_to_canonical_semantic_bytes(
    store: PostgresStore,
) -> None:
    """JSONB may rewrite text, but every block keeps one canonical fingerprint."""
    raw_blocks = _raw_blocks()
    result = parse_stage_b_response(
        json.dumps(raw_blocks, ensure_ascii=False),
        model="mock/stage-b",
        prompt_hash="stage-b-prompt",
        resume_hash="resume-b",
        cost_usd=0.02,
    )
    saved = await store.save_job(
        make_job(
            "stage-b-json",
            jd_text="Detailed JD",
            jd_quality=QualityBand.GOOD,
        )
    )
    await store.save_stage_a(saved.job_id, _stage_a())
    await store.save_stage_b(saved.job_id, result)

    stored = await _json_text_columns(store, saved.job_id)
    for column, block in zip(
        ("verdict", "summary", "fit", "hooks"),
        ("verdict", "jd_summary", "fit_analysis", "resume_hooks"),
        strict=True,
    ):
        assert _canonical_json_bytes(json.loads(stored[column])) == (
            _canonical_json_bytes(raw_blocks[block])
        )
    loaded = await store.get_evaluation(saved.job_id)
    assert loaded is not None
    assert loaded.stage_b is not None
    assert _canonical_json_bytes(loaded.stage_b.raw_blocks) == (
        _canonical_json_bytes(raw_blocks)
    )


async def test_postgres_rejects_invalid_json_without_damaging_saved_blocks(
    store: PostgresStore,
) -> None:
    """JSONB rejects invalid syntax atomically and preserves the prior value."""
    raw_blocks = _raw_blocks()
    result = parse_stage_b_response(
        json.dumps(raw_blocks, ensure_ascii=False),
        model="mock/stage-b",
        prompt_hash="stage-b-prompt",
        resume_hash="resume-b",
        cost_usd=0.02,
    )
    saved = await store.save_job(make_job("invalid-json"))
    await store.save_stage_a(saved.job_id, _stage_a())
    await store.save_stage_b(saved.job_id, result)
    before = await _json_text_columns(store, saved.job_id)
    pool = store._get_pool()

    async with pool.acquire() as conn:
        with pytest.raises(asyncpg.InvalidTextRepresentationError):
            await conn.execute(
                """UPDATE evaluations
                      SET stage_b_fit_json = $2::jsonb
                    WHERE job_id = $1""",
                int(saved.job_id),
                "{not-valid-json",
            )

    assert await _json_text_columns(store, saved.job_id) == before


def test_corrupt_json_text_raises_decode_error() -> None:
    """A TEXT-backed adapter must surface corrupt persisted JSON, not coerce it."""
    with pytest.raises(json.JSONDecodeError):
        _as_json_obj("{not-valid-json")
