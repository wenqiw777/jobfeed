"""Postgres canonical evaluation matches SQLite ownership behavior."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from jobfeed.adapters.store.postgres import PostgresStore
from jobfeed.domain.models import (
    FitAnalysis,
    JobPosting,
    MLGateResult,
    QualityBand,
    StageAResult,
    StageBResult,
    Verdict,
)

pytestmark = pytest.mark.postgres
EXPECTED_SCORE = 94
BACKFILL_SCORE = 88
EXPECTED_FIT_SCORE = 90


async def test_postgres_aliases_claim_once_and_source_audit_stays_empty(
    fresh_pg_dsn: str,
) -> None:
    store = PostgresStore(fresh_pg_dsn)
    await store.connect()
    try:
        ids = []
        for platform, key, url in (
            ("linkedin", "123", "https://www.linkedin.com/jobs/view/123/"),
            (
                "jobright",
                "other",
                "https://careers.southwestair.com/us/en/job/REQ123/engineer",
            ),
        ):
            saved = await store.save_job(
                JobPosting(
                    platform=platform,
                    canonical_id=key,
                    url=url,
                    title="Software Engineer",
                    company="Southwest Airlines",
                    location="Dallas, TX",
                    discovered_at=datetime.now(UTC),
                    jd_text="Build production software services and maintain APIs. "
                    * 8,
                    jd_quality=QualityBand.FULL,
                )
            )
            ids.append(saved.job_id)
        real_id = await store.resolve_real_job_id(ids[0])
        assert real_id == await store.resolve_real_job_id(ids[1])
        claims = await store.claim_real_job_stage_a_by_ids([real_id, real_id])
        assert len(claims) == 1
        assert claims[0].source_job_id == ids[1]
        saved = await store.save_real_job_stage_a(
            real_id,
            StageAResult(
                score=94,
                one_line="Fit",
                timing_eligible="yes",
                model="test",
                prompt_hash="existing",
                resume_hash="existing",
            ),
            expected_revision=claims[0].input_revision,
            expected_generation=claims[0].claim_generation,
        )
        assert saved
        stage_b = await store.claim_real_job_stage_b_by_ids(
            [real_id], stage_a_threshold=80
        )
        assert len(stage_b) == 1
        assert await store.refresh_real_job_stage_b_claim(
            real_id,
            expected_revision=stage_b[0].input_revision,
            expected_generation=stage_b[0].claim_generation,
        )
        assert await store.save_real_job_stage_b(
            real_id,
            StageBResult(
                verdict=Verdict.APPLY,
                jd_summary="Software role",
                fit_analysis=FitAnalysis(score=90, strengths=[], gaps=[]),
                resume_hooks=[],
                model="test",
                prompt_hash="existing",
                resume_hash="existing",
            ),
            expected_revision=stage_b[0].input_revision,
            expected_generation=stage_b[0].claim_generation,
        )
        assert (
            await store.claim_real_job_stage_b_by_ids([real_id], stage_a_threshold=80)
            == []
        )
        assert await store.claim_real_job_stage_a_by_ids([real_id]) == []
        async with store._get_pool().acquire() as db:
            await db.execute(
                "UPDATE jobs SET jd_text=$1 WHERE id=$2",
                "Build Python production services and APIs. " * 8,
                int(ids[1]),
            )
        # Even a new official JD does not authorize re-scoring a completed job.
        assert await store.claim_real_job_stage_a_by_ids([real_id]) == []
        async with store._get_pool().acquire() as db:
            current = await db.fetchrow(
                "SELECT stage_a_status,stage_a_score,stage_b_status "
                "FROM real_job_evaluations WHERE real_job_id=$1",
                int(real_id),
            )
            assert tuple(current.values()) == ("completed", EXPECTED_SCORE, "completed")
            history = await db.fetchrow(
                "SELECT stage_a_score,stage_b_verdict,reason "
                "FROM real_job_evaluation_history WHERE real_job_id=$1",
                int(real_id),
            )
            assert history is None
            assert (
                await db.fetchval(
                    "SELECT COUNT(*) FROM evaluations WHERE job_id=ANY($1::int[])",
                    [int(x) for x in ids],
                )
                == 0
            )
    finally:
        await store.close()


async def test_postgres_activation_requires_marker_and_complete_parent_state(
    fresh_pg_dsn: str,
) -> None:
    store = PostgresStore(fresh_pg_dsn)
    await store.connect()
    try:
        assert not await store.canonical_evaluation_ready()
        saved = await store.save_job(
            JobPosting(
                platform="linkedin",
                canonical_id="activation",
                url="https://example.test/activation",
                title="Engineer",
                company="Acme",
                location="Remote",
                discovered_at=datetime.now(UTC),
            )
        )
        await store.set_state("real_job_evaluation_activation_v1", "enabled")
        assert await store.canonical_evaluation_ready()
        async with store._get_pool().acquire() as db:
            await db.execute(
                "UPDATE jobs SET real_job_id=NULL WHERE id=$1", int(saved.job_id)
            )
        with pytest.raises(ValueError, match="orphan"):
            await store.canonical_evaluation_ready()
    finally:
        await store.close()


async def test_postgres_failed_claim_is_retryable(fresh_pg_dsn: str) -> None:
    store = PostgresStore(fresh_pg_dsn)
    await store.connect()
    try:
        saved = await store.save_job(
            JobPosting(
                platform="linkedin",
                canonical_id="retry",
                url="https://example.test/retry",
                title="Engineer",
                company="Acme",
                location="Remote",
                discovered_at=datetime.now(UTC),
                jd_text="Build production software services and APIs. " * 10,
                jd_quality=QualityBand.FULL,
            )
        )
        real_id = await store.resolve_real_job_id(saved.job_id)
        claim = (await store.claim_real_job_stage_a_by_ids([real_id]))[0]
        assert await store.save_real_job_stage_a_error(
            real_id,
            "timeout",
            expected_revision=claim.input_revision,
            expected_generation=claim.claim_generation,
        )
        assert len(await store.claim_real_job_stage_a_by_ids([real_id])) == 1
    finally:
        await store.close()


async def test_postgres_source_write_preserves_completed_score(
    fresh_pg_dsn: str,
) -> None:
    store = PostgresStore(fresh_pg_dsn)
    await store.connect()
    try:
        source = JobPosting(
            platform="linkedin",
            canonical_id="changed",
            url="https://example.test/changed",
            title="Engineer",
            company="Acme",
            location="Remote",
            discovered_at=datetime.now(UTC),
            jd_text="Build Java services and APIs. " * 15,
            jd_quality=QualityBand.FULL,
        )
        saved = await store.save_job(source)
        real_id = await store.resolve_real_job_id(saved.job_id)
        claim = (await store.claim_real_job_stage_a_by_ids([real_id]))[0]
        assert await store.save_real_job_stage_a(
            real_id,
            StageAResult(
                score=90,
                one_line="Fit",
                timing_eligible="yes",
                model="test",
                prompt_hash="existing",
                resume_hash="existing",
            ),
            expected_revision=claim.input_revision,
            expected_generation=claim.claim_generation,
        )
        await store.save_job(
            replace(source, jd_text="Build Python services and APIs. " * 15)
        )
        async with store._get_pool().acquire() as db:
            row = await db.fetchrow(
                "SELECT stage_a_score,stage_a_status FROM real_job_evaluations "
                "WHERE real_job_id=$1",
                int(real_id),
            )
            assert row["stage_a_score"] == 90  # noqa: PLR2004 - saved result above
            assert row["stage_a_status"] == "completed"
            assert (
                await db.fetchval("SELECT COUNT(*) FROM real_job_evaluation_history")
                == 0
            )
    finally:
        await store.close()


async def test_postgres_source_evaluation_backfill_reuses_existing_paid_score(
    fresh_pg_dsn: str,
) -> None:
    store = PostgresStore(fresh_pg_dsn)
    await store.connect()
    try:
        saved = await store.save_job(
            JobPosting(
                platform="linkedin",
                canonical_id="legacy-score",
                url="https://example.test/legacy-score",
                title="Engineer",
                company="Acme",
                location="Remote",
                discovered_at=datetime.now(UTC),
                enriched_at=datetime.now(UTC) - timedelta(days=1),
                jd_text="Build production software services and APIs. " * 10,
                jd_quality=QualityBand.FULL,
            )
        )
        await store.save_stage_a(
            saved.job_id,
            StageAResult(
                score=88,
                one_line="Existing fit",
                timing_eligible="yes",
                model="legacy",
                prompt_hash="existing",
                resume_hash="existing",
            ),
        )
        await store.save_stage_b(
            saved.job_id,
            StageBResult(
                verdict=Verdict.APPLY,
                jd_summary="Existing summary",
                fit_analysis=FitAnalysis(score=90, strengths=[], gaps=[]),
                resume_hooks=[],
                model="legacy",
                prompt_hash="existing",
                resume_hash="existing",
            ),
        )
        real_id = await store.resolve_real_job_id(saved.job_id)
        await store.set_state("real_job_evaluation_activation_v1", "enabled")
        with pytest.raises(ValueError, match="evaluation backfill"):
            await store.canonical_evaluation_ready()
        assert await store.backfill_real_job_evaluations(limit=100) == (int(real_id), 1)
        assert await store.canonical_evaluation_ready()
        assert await store.backfill_real_job_evaluations(limit=100) == (int(real_id), 0)
        assert await store.claim_real_job_stage_a_by_ids([real_id]) == []
        assert (
            await store.claim_real_job_stage_b_by_ids([real_id], stage_a_threshold=80)
            == []
        )
        async with store._get_pool().acquire() as db:
            assert (
                await db.fetchval(
                    "SELECT stage_a_score FROM real_job_evaluations "
                    "WHERE real_job_id=$1",
                    int(real_id),
                )
                == BACKFILL_SCORE
            )
    finally:
        await store.close()


async def test_postgres_format_only_source_update_keeps_paid_score(
    fresh_pg_dsn: str,
) -> None:
    store = PostgresStore(fresh_pg_dsn)
    await store.connect()
    try:
        source = JobPosting(
            platform="linkedin",
            canonical_id="format-only",
            url="https://example.test/format-only",
            title="Engineer",
            company="Acme",
            location="Remote",
            discovered_at=datetime.now(UTC),
            jd_text="Build production services and APIs. " * 15,
            jd_quality=QualityBand.FULL,
        )
        saved = await store.save_job(source)
        real_id = await store.resolve_real_job_id(saved.job_id)
        claim = (await store.claim_real_job_stage_a_by_ids([real_id]))[0]
        await store.save_real_job_stage_a(
            real_id,
            StageAResult(
                score=88,
                one_line="Fit",
                timing_eligible="yes",
                model="test",
                prompt_hash="existing",
                resume_hash="existing",
            ),
            expected_revision=claim.input_revision,
            expected_generation=claim.claim_generation,
        )
        await store.save_job(replace(source, jd_text=source.jd_text.replace(" ", "\n")))
        assert await store.claim_real_job_stage_a_by_ids([real_id]) == []
        async with store._get_pool().acquire() as db:
            assert tuple(
                await db.fetchrow(
                    "SELECT input_revision,stage_a_score FROM real_job_evaluations "
                    "WHERE real_job_id=$1",
                    int(real_id),
                )
            ) == (1, 88)
    finally:
        await store.close()


async def test_postgres_gate_fail_can_be_reconsidered_after_policy_change(
    fresh_pg_dsn: str,
) -> None:
    store = PostgresStore(fresh_pg_dsn)
    await store.connect()
    try:
        saved = await store.save_job(
            JobPosting(
                platform="linkedin",
                canonical_id="gate-policy",
                url="https://example.test/gate-policy",
                title="Engineer",
                company="Acme",
                location="Remote",
                discovered_at=datetime.now(UTC),
                jd_text="Build production software services and APIs. " * 10,
                jd_quality=QualityBand.FULL,
            )
        )
        real_id = await store.resolve_real_job_id(saved.job_id)
        claim = (await store.claim_real_job_stage_a_by_ids([real_id]))[0]
        await store.save_real_job_ml_gate(
            real_id,
            MLGateResult(
                score=0.1, result="fail", fail_reason="not software engineering role"
            ),
            expected_revision=claim.input_revision,
            expected_generation=claim.claim_generation,
        )
        await store.release_real_job_stage_a_claim(
            real_id,
            expected_revision=claim.input_revision,
            expected_generation=claim.claim_generation,
        )
        assert await store.list_real_job_ids_for_evaluation(
            limit=10, stage="a", threshold=80
        ) == [real_id]
        assert len(await store.claim_real_job_stage_a_by_ids([real_id])) == 1
    finally:
        await store.close()


async def test_postgres_canonical_backlog_keyset_pages(fresh_pg_dsn: str) -> None:
    store = PostgresStore(fresh_pg_dsn)
    await store.connect()
    try:
        real_ids = []
        for key in ("one", "two"):
            saved = await store.save_job(
                JobPosting(
                    platform="linkedin",
                    canonical_id=key,
                    url=f"https://example.test/{key}",
                    title=f"Engineer {key}",
                    company="Acme",
                    location="Remote",
                    discovered_at=datetime.now(UTC),
                    jd_text="Build production software services and APIs. " * 10,
                    jd_quality=QualityBand.FULL,
                )
            )
            real_ids.append(await store.resolve_real_job_id(saved.job_id))
        newest = await store.list_real_job_ids_for_evaluation(
            limit=1,
            stage="a",
            threshold=80,
        )
        older = await store.list_real_job_ids_for_evaluation(
            limit=1,
            stage="a",
            threshold=80,
            before_id=int(newest[-1]),
        )
        assert newest + older == list(reversed(real_ids))
    finally:
        await store.close()


async def test_postgres_reclaimed_stage_b_rejects_old_worker_same_input(
    fresh_pg_dsn: str,
) -> None:
    store = PostgresStore(fresh_pg_dsn)
    await store.connect()
    try:
        saved = await store.save_job(
            JobPosting(
                platform="linkedin",
                canonical_id="generation",
                url="https://example.test/generation",
                title="Engineer",
                company="Acme",
                location="Remote",
                discovered_at=datetime.now(UTC),
                jd_text="Build production software services and APIs. " * 10,
                jd_quality=QualityBand.FULL,
            )
        )
        real_id = await store.resolve_real_job_id(saved.job_id)
        stage_a = (await store.claim_real_job_stage_a_by_ids([real_id]))[0]
        await store.save_real_job_stage_a(
            real_id,
            StageAResult(
                score=90,
                one_line="Fit",
                timing_eligible="yes",
                model="test",
                prompt_hash="existing",
                resume_hash="existing",
            ),
            expected_revision=stage_a.input_revision,
            expected_generation=stage_a.claim_generation,
        )
        old = (
            await store.claim_real_job_stage_b_by_ids(
                [real_id],
                stage_a_threshold=80,
            )
        )[0]
        async with store._get_pool().acquire() as db:
            await db.execute(
                "UPDATE real_job_evaluations SET updated_at=now()-interval '2 hours' "
                "WHERE real_job_id=$1",
                int(real_id),
            )
        fresh = (
            await store.claim_real_job_stage_b_by_ids(
                [real_id],
                stage_a_threshold=80,
            )
        )[0]
        assert fresh.input_revision == old.input_revision
        assert fresh.claim_generation > old.claim_generation
        assert not await store.refresh_real_job_stage_b_claim(
            real_id,
            expected_revision=old.input_revision,
            expected_generation=old.claim_generation,
        )
        assert await store.refresh_real_job_stage_b_claim(
            real_id,
            expected_revision=fresh.input_revision,
            expected_generation=fresh.claim_generation,
        )
        result = StageBResult(
            verdict=Verdict.APPLY,
            jd_summary="Fit",
            fit_analysis=FitAnalysis(score=90, strengths=[], gaps=[]),
            resume_hooks=[],
            model="test",
            prompt_hash="existing",
            resume_hash="existing",
        )
        assert not await store.save_real_job_stage_b(
            real_id,
            result,
            expected_revision=old.input_revision,
            expected_generation=old.claim_generation,
        )
        assert await store.save_real_job_stage_b(
            real_id,
            result,
            expected_revision=fresh.input_revision,
            expected_generation=fresh.claim_generation,
        )
    finally:
        await store.close()


@pytest.mark.parametrize(
    ("case", "expected_hold"),
    [
        ("different_jd", "clear"),
        ("different_scores", "clear"),
        ("empty_jd", "evaluation_input_missing"),
        ("scored_empty_alias", "evaluation_input_missing"),
        ("jd_changed_after_score", "clear"),
        ("unknown_input_time", "clear"),
    ],
)
async def test_postgres_unverifiable_legacy_score_is_held(
    fresh_pg_dsn: str,
    case: str,
    expected_hold: str,
) -> None:
    store = PostgresStore(fresh_pg_dsn)
    await store.connect()
    try:
        now = datetime.now(UTC)
        ats = "https://boards.greenhouse.io/acme/jobs/1234567"
        first = await store.save_job(
            JobPosting(
                platform="linkedin",
                canonical_id=f"{case}-one",
                url=f"https://www.linkedin.com/jobs/view/{case}-one/",
                apply_url=ats,
                title="Engineer",
                company="Acme",
                location="Remote",
                discovered_at=now,
                enriched_at=now - timedelta(days=1),
                jd_text=(
                    ""
                    if case in {"empty_jd", "scored_empty_alias"}
                    else "Build Java services and APIs. " * 15
                ),
                jd_quality=QualityBand.GOOD
                if case in {"empty_jd", "scored_empty_alias"}
                else QualityBand.FULL,
            )
        )
        await store.save_stage_a(
            first.job_id,
            StageAResult(
                score=84,
                one_line="Existing fit",
                timing_eligible="yes",
                model="legacy",
                prompt_hash="existing",
                resume_hash="existing",
            ),
        )
        second = None
        if case in {"different_jd", "different_scores", "scored_empty_alias"}:
            second = await store.save_job(
                JobPosting(
                    platform="jobright",
                    canonical_id=f"{case}-two",
                    url=ats,
                    title="Engineer",
                    company="Acme",
                    location="Remote",
                    discovered_at=now,
                    enriched_at=now - timedelta(days=1),
                    jd_text=(
                        "Build Java services and APIs. " * 15
                        if case == "scored_empty_alias"
                        else "Build Python services and APIs. " * 15
                    ),
                    jd_quality=QualityBand.FULL,
                )
            )
        if case == "different_scores":
            await store.save_stage_a(
                second.job_id,
                StageAResult(
                    score=30,
                    one_line="Different fit",
                    timing_eligible="no",
                    model="legacy",
                    prompt_hash="existing",
                    resume_hash="existing",
                ),
            )
        if case == "jd_changed_after_score":
            async with store._get_pool().acquire() as db:
                await db.execute(
                    "UPDATE jobs SET enriched_at=now()+interval '1 day' WHERE id=$1",
                    int(first.job_id),
                )
        if case == "unknown_input_time":
            async with store._get_pool().acquire() as db:
                await db.execute(
                    "UPDATE jobs SET enriched_at=NULL WHERE id=$1", int(first.job_id)
                )
        real_id = await store.resolve_real_job_id(first.job_id)
        if second is not None:
            assert await store.resolve_real_job_id(second.job_id) == real_id
        await store.set_state("real_job_evaluation_activation_v1", "enabled")
        with pytest.raises(ValueError, match="evaluation backfill"):
            await store.canonical_evaluation_ready()
        assert (await store.backfill_real_job_evaluations(limit=100))[1] == int(
            expected_hold == "clear"
        )
        assert await store.canonical_evaluation_ready()
        if expected_hold == "clear":
            detail = await store.get_real_job_view(real_id)
            assert detail is not None
            assert detail["row"]["identity_review_state"] == "clear"
            assert detail["row"]["stage_a_score"] == 84  # noqa: PLR2004 - fixture score
            assert await store.claim_real_job_stage_a_by_ids([real_id]) == []
            return
        assert await store.claim_real_job_stage_a_by_ids([real_id]) == []
        assert (
            await store.claim_real_job_stage_b_by_ids([real_id], stage_a_threshold=80)
            == []
        )
        detail = await store.get_real_job_view(real_id)
        assert detail is not None
        assert detail["row"]["identity_review_state"] == expected_hold
        assert detail["row"]["stage_a_score"] is None
        async with store._get_pool().acquire() as db:
            state = await db.fetchval(
                "SELECT identity_review_state FROM real_jobs WHERE id=$1",
                int(real_id),
            )
            current_count = await db.fetchval(
                "SELECT COUNT(*) FROM real_job_evaluations WHERE real_job_id=$1",
                int(real_id),
            )
            audit_count = await db.fetchval(
                "SELECT COUNT(*) FROM evaluations e JOIN jobs j ON j.id=e.job_id "
                "WHERE j.real_job_id=$1 AND e.stage_a_status='completed'",
                int(real_id),
            )
        assert state == expected_hold
        assert current_count == 0
        assert audit_count == (2 if case == "different_scores" else 1)
    finally:
        await store.close()


async def test_postgres_stage_b_does_not_claim_existing_score_on_hold(
    fresh_pg_dsn: str,
) -> None:
    store = PostgresStore(fresh_pg_dsn)
    await store.connect()
    try:
        saved = await store.save_job(
            JobPosting(
                platform="linkedin",
                canonical_id="stage-b-hold",
                url="https://example.test/stage-b-hold",
                title="Engineer",
                company="Acme",
                location="Remote",
                discovered_at=datetime.now(UTC),
                jd_text="Build production services and APIs. " * 12,
                jd_quality=QualityBand.FULL,
            )
        )
        real_id = await store.resolve_real_job_id(saved.job_id)
        claim = (await store.claim_real_job_stage_a_by_ids([real_id]))[0]
        assert await store.save_real_job_stage_a(
            real_id,
            StageAResult(
                score=84,
                one_line="Fit",
                timing_eligible="yes",
                model="test",
                prompt_hash="existing",
                resume_hash="existing",
            ),
            expected_revision=claim.input_revision,
            expected_generation=claim.claim_generation,
        )
        async with store._get_pool().acquire() as db:
            await db.execute(
                "UPDATE real_jobs SET "
                "identity_review_state='evaluation_input_conflict' "
                "WHERE id=$1",
                int(real_id),
            )
        assert (
            await store.claim_real_job_stage_b_by_ids([real_id], stage_a_threshold=80)
            == []
        )
    finally:
        await store.close()


async def test_postgres_corrected_source_jd_clears_requirements_hold(
    fresh_pg_dsn: str,
) -> None:
    store = PostgresStore(fresh_pg_dsn)
    await store.connect()
    try:
        now = datetime.now(UTC)
        ats = "https://boards.greenhouse.io/acme/jobs/1234567"
        first = JobPosting(
            platform="linkedin",
            canonical_id="corrected-first",
            url="https://jobs.lever.co/acme/12345678-1234-1234-1234-123456789012",
            apply_url=ats,
            title="Engineer",
            company="Acme",
            location="Remote",
            discovered_at=now,
            jd_text="Build Java services and APIs. " * 15,
            jd_quality=QualityBand.FULL,
        )
        second = JobPosting(
            platform="jobright",
            canonical_id="corrected-second",
            url=ats,
            title="Engineer",
            company="Acme",
            location="Remote",
            discovered_at=now,
            jd_text="Build Python services and APIs. " * 15,
            jd_quality=QualityBand.FULL,
        )
        saved = await store.save_job(first)
        await store.save_job(second)
        real_id = await store.resolve_real_job_id(saved.job_id)
        assert await store.claim_real_job_stage_a_by_ids([real_id]) == []
        await store.save_job(replace(second, jd_text=first.jd_text))
        detail = await store.get_real_job_view(real_id)
        assert detail is not None
        assert detail["row"]["identity_review_state"] == "clear"
        assert len(await store.claim_real_job_stage_a_by_ids([real_id])) == 1
    finally:
        await store.close()


async def test_policy_change_preserves_completed_stages_postgres(
    fresh_pg_dsn: str,
) -> None:
    store = PostgresStore(fresh_pg_dsn)
    await store.connect()
    try:
        saved = await store.save_job(
            JobPosting(
                platform="linkedin",
                canonical_id="policy-change",
                url="https://example.test/policy-change",
                title="Engineer",
                company="Acme",
                location="Remote",
                discovered_at=datetime.now(UTC),
                jd_text="Build production services and APIs. " * 12,
                jd_quality=QualityBand.FULL,
            )
        )
        real_id = await store.resolve_real_job_id(saved.job_id)
        a1 = {"model": "quick-v1", "gate_model": "gate-v1"}
        a2 = {"model": "quick-v1", "gate_model": "gate-v2"}
        b1 = {"model": "detail-v1"}
        b2 = {"model": "detail-v2"}
        first = (
            await store.claim_real_job_stage_a_by_ids(
                [real_id], stage_a_policy=a1, stage_b_policy=b1
            )
        )[0]
        assert await store.save_real_job_stage_a(
            real_id,
            StageAResult(
                score=88,
                one_line="Fit",
                timing_eligible="yes",
                model="quick-v1",
                prompt_hash="existing",
                resume_hash="existing",
            ),
            expected_revision=first.input_revision,
            expected_generation=first.claim_generation,
        )
        assert (
            await store.claim_real_job_stage_a_by_ids(
                [real_id], stage_a_policy=a1, stage_b_policy=b1
            )
            == []
        )
        assert (
            await store.list_real_job_ids_for_evaluation(
                limit=10, stage="a", stage_a_policy=a2
            )
            == []
        )
        detailed = (
            await store.claim_real_job_stage_b_by_ids(
                [real_id],
                stage_a_threshold=80,
                stage_a_policy=a1,
                stage_b_policy=b1,
            )
        )[0]
        assert await store.save_real_job_stage_b(
            real_id,
            StageBResult(
                verdict=Verdict.APPLY,
                jd_summary="Fit",
                fit_analysis=FitAnalysis(score=90, strengths=[], gaps=[]),
                resume_hooks=[],
                model="detail-v1",
                prompt_hash="existing",
                resume_hash="existing",
            ),
            expected_revision=detailed.input_revision,
            expected_generation=detailed.claim_generation,
        )
        assert (
            await store.claim_real_job_stage_b_by_ids(
                [real_id],
                stage_a_threshold=80,
                stage_a_policy=a1,
                stage_b_policy=b1,
            )
            == []
        )
        stale_b = await store.get_real_job_view(
            real_id, stage_a_policy=a1, stage_b_policy=b2
        )
        assert stale_b is not None
        assert stale_b["row"]["stage_a_score"] == BACKFILL_SCORE
        assert stale_b["row"]["stage_b_verdict"] == "apply"
        stale_a = await store.get_real_job_view(
            real_id, stage_a_policy=a2, stage_b_policy=b1
        )
        assert stale_a is not None
        assert stale_a["row"]["stage_a_score"] == BACKFILL_SCORE
        assert stale_a["row"]["stage_b_verdict"] == "apply"
        stale_list = await store.query_real_jobs_view(
            decision="results", stage_a_policy=a2, stage_b_policy=b1
        )
        assert stale_list["jobs"][0]["stage_a_score"] == BACKFILL_SCORE
        assert stale_list["jobs"][0]["score"] == EXPECTED_FIT_SCORE
        assert stale_list["jobs"][0]["evaluation_stale_reason"] is None
        pending_results = await store.query_real_jobs_view(
            decision="results",
            require_verdict=True,
            stage_a_policy=a2,
            stage_b_policy=b1,
        )
        assert pending_results["total"] == 1
        searched = await store.query_real_jobs_view(
            decision="results",
            search="Acme",
            stage_a_policy=a2,
            stage_b_policy=b1,
        )
        assert searched["total"] == 1
        assert searched["jobs"][0]["stage_a_score"] == BACKFILL_SCORE
        stale_library = await store.query_source_library(
            decision=None,
            sort="score_desc",
            search=None,
            limit=25,
            offset=0,
            stage_a_policy=a2,
            stage_b_policy=b1,
        )
        assert stale_library["jobs"][0]["score"] == EXPECTED_FIT_SCORE
        stale_priority = await store.load_real_job_priority_inputs(
            [real_id], stage_a_policy=a2, stage_b_policy=b1
        )
        assert stale_priority[0].stage_a_score == BACKFILL_SCORE
        assert stale_priority[0].stage_b_fit_score == EXPECTED_FIT_SCORE
        pending = await store.canonical_policy_pending_counts(
            stage_a_policy=a2, stage_b_policy=b1
        )
        assert pending["stage_a_pending"] == 0
        assert await store.canonical_policy_cutover_ready(
            stage_a_policy=a2, stage_b_policy=b1
        )
        async with store._get_pool().acquire() as db:
            previous_facts = await db.fetchval(
                "SELECT input_facts_json FROM real_job_evaluations "
                "WHERE real_job_id=$1",
                int(real_id),
            )
            await db.execute(
                "UPDATE real_job_evaluations SET input_facts_json='{}' "
                "WHERE real_job_id=$1",
                int(real_id),
            )
        legacy_page = await store.query_real_jobs_view(
            decision="results",
            require_verdict=True,
            stage_a_policy=a2,
            stage_b_policy=b1,
        )
        assert legacy_page["total"] == 1
        assert legacy_page["jobs"][0]["evaluation_stale_reason"] is None
        assert legacy_page["jobs"][0]["stage_a_score"] == BACKFILL_SCORE
        assert legacy_page["jobs"][0]["stage_b_verdict"] == "apply"
        legacy_detail = await store.get_real_job_view(
            real_id,
            stage_a_policy=a2,
            stage_b_policy=b1,
        )
        assert legacy_detail["row"]["evaluation_stale_reason"] is None
        assert legacy_detail["row"]["stage_a_score"] == BACKFILL_SCORE
        legacy = await store.canonical_policy_pending_counts(
            stage_a_policy=a2,
            stage_b_policy=b1,
        )
        assert legacy == {
            "stage_a_pending": 0,
            "stage_b_pending": 0,
            "legacy_stage_a": 1,
            "legacy_stage_b": 1,
        }
        async with store._get_pool().acquire() as db:
            await db.execute(
                "UPDATE real_job_evaluations SET input_facts_json=$1 "
                "WHERE real_job_id=$2",
                previous_facts,
                int(real_id),
            )
        assert (
            await store.claim_real_job_stage_b_by_ids(
                [real_id], stage_a_threshold=80, stage_a_policy=a2, stage_b_policy=b2
            )
            == []
        )
        assert (
            await store.claim_real_job_stage_a_by_ids(
                [real_id], stage_a_policy=a2, stage_b_policy=b2
            )
            == []
        )
        unchanged = await store.get_real_job_view(
            real_id, stage_a_policy=a2, stage_b_policy=b2
        )
        assert unchanged is not None
        assert unchanged["row"]["stage_a_score"] == BACKFILL_SCORE
        assert unchanged["row"]["stage_b_verdict"] == "apply"
    finally:
        await store.close()
