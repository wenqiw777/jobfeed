"""Canonical paid-work ownership is independent of source evaluation audit."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import aiosqlite
import pytest

import jobfeed.services._evaluate_canonical as canonical_module
from jobfeed.adapters.store._sqlite_real_job_evaluation import (
    sync_sqlite_real_job_input,
)
from jobfeed.adapters.store._sqlite_real_job_identity import _merge
from jobfeed.adapters.store.sqlite import SQLiteStore
from jobfeed.adapters.store.sqlite_schema import migrate_real_jobs_schema
from jobfeed.domain.models import (
    FitAnalysis,
    JobPosting,
    MLGateResult,
    QualityBand,
    StageAResult,
    StageBResult,
    Verdict,
)
from jobfeed.services._evaluate_canonical import _maintain_real_job_stage_b_claim

EXPECTED_SCORE = 94
BACKFILL_SCORE = 88
EXPECTED_FIT_SCORE = 90


@pytest.mark.asyncio
async def test_two_source_aliases_claim_and_score_once(tmp_path: Path) -> None:
    path = tmp_path / "canonical.db"
    store = SQLiteStore(path)
    await store.connect()
    try:
        source_ids = []
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
            source_ids.append(saved.job_id)
        real_id = await store.resolve_real_job_id(source_ids[0])
        assert real_id == await store.resolve_real_job_id(source_ids[1])
        claims = await store.claim_real_job_stage_a_by_ids([real_id, real_id])
        assert len(claims) == 1
        assert claims[0].real_job_id == real_id
        assert claims[0].source_job_id == source_ids[1]
        await store.save_real_job_stage_a(
            real_id,
            StageAResult(
                score=94,
                one_line="Fit",
                timing_eligible="yes",
                model="test",
                prompt_hash="existing-result-contract",
                resume_hash="existing-result-contract",
            ),
            expected_revision=claims[0].input_revision,
            expected_generation=claims[0].claim_generation,
        )
        assert await store.claim_real_job_stage_a_by_ids([real_id]) == []
        async with aiosqlite.connect(path) as db:
            assert (
                await (
                    await db.execute(
                        "SELECT stage_a_score FROM real_job_evaluations "
                        "WHERE real_job_id=?",
                        (int(real_id),),
                    )
                ).fetchone()
            )[0] == EXPECTED_SCORE
            assert (
                await (
                    await db.execute(
                        "SELECT COUNT(*) FROM evaluations WHERE job_id IN (?,?)",
                        tuple(map(int, source_ids)),
                    )
                ).fetchone()
            )[0] == 0
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_failed_claim_can_retry_without_duplicate_current_score(
    tmp_path: Path,
) -> None:
    store = SQLiteStore(tmp_path / "retry.db")
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
        first = (await store.claim_real_job_stage_a_by_ids([real_id]))[0]
        assert await store.save_real_job_stage_a_error(
            real_id,
            "timeout",
            expected_revision=first.input_revision,
            expected_generation=first.claim_generation,
        )
        second = (await store.claim_real_job_stage_a_by_ids([real_id]))[0]
        assert second.input_revision == first.input_revision
        assert await store.release_real_job_stage_a_claim(
            real_id,
            expected_revision=second.input_revision,
            expected_generation=second.claim_generation,
        )
        assert len(await store.claim_real_job_stage_a_by_ids([real_id])) == 1
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_identity_merge_preserves_both_canonical_evaluation_receipts(
    tmp_path: Path,
) -> None:
    path = tmp_path / "merge.db"
    store = SQLiteStore(path)
    await store.connect()
    try:
        real_ids = []
        for key in ("one", "two"):
            saved = await store.save_job(
                JobPosting(
                    platform="linkedin",
                    canonical_id=key,
                    url=f"https://example.test/{key}",
                    title="Engineer",
                    company="Acme",
                    location="Remote",
                    discovered_at=datetime.now(UTC),
                    jd_text=f"Build production services and APIs for {key}. " * 12,
                    jd_quality=QualityBand.FULL,
                )
            )
            real_id = await store.resolve_real_job_id(saved.job_id)
            real_ids.append(real_id)
            claim = (await store.claim_real_job_stage_a_by_ids([real_id]))[0]
            assert await store.save_real_job_stage_a(
                real_id,
                StageAResult(
                    score=80,
                    one_line="Fit",
                    timing_eligible="yes",
                    model="test",
                    prompt_hash="existing",
                    resume_hash="existing",
                ),
                expected_revision=claim.input_revision,
                expected_generation=claim.claim_generation,
            )
        async with aiosqlite.connect(path) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("BEGIN IMMEDIATE")
            await _merge(db, *map(int, real_ids))
            await db.commit()
            assert (
                await (
                    await db.execute("SELECT COUNT(*) FROM real_job_evaluations")
                ).fetchone()
            )[0] == 1
            assert (
                await (
                    await db.execute("SELECT COUNT(*) FROM real_job_evaluation_history")
                ).fetchone()
            )[0] == 1
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_merge_of_legacy_scores_keeps_activation_ready(tmp_path: Path) -> None:
    path = tmp_path / "legacy-merge.db"
    store = SQLiteStore(path)
    await store.connect()
    try:
        source_ids = []
        real_ids = []
        for key, score in (("one", 35), ("two", 40)):
            saved = await store.save_job(
                JobPosting(
                    platform="linkedin",
                    canonical_id=key,
                    url=f"https://example.test/{key}",
                    title="Engineer",
                    company="Acme",
                    location="Remote",
                    discovered_at=datetime.now(UTC),
                    enriched_at=datetime.now(UTC),
                    jd_text=f"Build production services and APIs for {key}. " * 12,
                    jd_quality=QualityBand.FULL,
                )
            )
            source_ids.append(saved.job_id)
            real_ids.append(await store.resolve_real_job_id(saved.job_id))
            await store.save_stage_a(
                saved.job_id,
                StageAResult(
                    score=score,
                    one_line="Legacy score",
                    timing_eligible="yes",
                    model="legacy",
                    prompt_hash="existing",
                    resume_hash="existing",
                ),
            )
        await store.set_state("real_job_evaluation_activation_v1", "enabled")
        async with aiosqlite.connect(path) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("BEGIN IMMEDIATE")
            await _merge(db, *map(int, real_ids))
            await sync_sqlite_real_job_input(db, int(source_ids[0]))
            await db.commit()
            state = await (
                await db.execute(
                    "SELECT identity_review_state FROM real_jobs WHERE id=?",
                    (int(real_ids[0]),),
                )
            ).fetchone()
            assert state[0] == "evaluation_input_conflict"
        assert await store.canonical_evaluation_ready()
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_stage_b_is_claimed_and_saved_by_real_id(tmp_path: Path) -> None:
    path = tmp_path / "stage-b.db"
    store = SQLiteStore(path)
    await store.connect()
    try:
        saved = await store.save_job(
            JobPosting(
                platform="linkedin",
                canonical_id="200",
                url="https://www.linkedin.com/jobs/view/200/",
                title="Engineer",
                company="Acme",
                location="Remote",
                discovered_at=datetime.now(UTC),
                jd_text="Build Java services and APIs. " * 15,
                jd_quality=QualityBand.FULL,
            )
        )
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
        stage_b = await store.claim_real_job_stage_b_by_ids(
            [real_id], stage_a_threshold=80
        )
        assert len(stage_b) == 1
        assert await store.refresh_real_job_stage_b_claim(
            real_id,
            expected_revision=stage_b[0].input_revision,
            expected_generation=stage_b[0].claim_generation,
        )
        result = StageBResult(
            verdict=Verdict.APPLY,
            jd_summary="Java role",
            fit_analysis=FitAnalysis(score=90, strengths=[], gaps=[]),
            resume_hooks=[],
            model="test",
            prompt_hash="existing",
            resume_hash="existing",
        )
        assert await store.save_real_job_stage_b(
            real_id,
            result,
            expected_revision=stage_b[0].input_revision,
            expected_generation=stage_b[0].claim_generation,
        )
        assert (
            await store.claim_real_job_stage_b_by_ids([real_id], stage_a_threshold=80)
            == []
        )
        async with aiosqlite.connect(path) as db:
            assert (
                await (
                    await db.execute(
                        "SELECT stage_b_verdict FROM real_job_evaluations "
                        "WHERE real_job_id=?",
                        (int(real_id),),
                    )
                ).fetchone()
            )[0] == "apply"
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_substantive_input_change_fences_old_paid_response(
    tmp_path: Path,
) -> None:
    path = tmp_path / "stale.db"
    store = SQLiteStore(path)
    await store.connect()
    try:
        saved = await store.save_job(
            JobPosting(
                platform="linkedin",
                canonical_id="100",
                url="https://www.linkedin.com/jobs/view/100/",
                title="Engineer",
                company="Acme",
                location="Remote",
                discovered_at=datetime.now(UTC),
                jd_text="Build Java services and APIs. " * 15,
                jd_quality=QualityBand.FULL,
            )
        )
        real_id = await store.resolve_real_job_id(saved.job_id)
        first = (await store.claim_real_job_stage_a_by_ids([real_id]))[0]
        async with aiosqlite.connect(path) as db:
            await db.execute(
                "UPDATE jobs SET jd_text=? WHERE id=?",
                ("Build Python services and APIs. " * 15, int(saved.job_id)),
            )
            await db.commit()
        second = (await store.claim_real_job_stage_a_by_ids([real_id]))[0]
        assert second.input_revision == first.input_revision + 1
        result = StageAResult(
            score=80,
            one_line="Fit",
            timing_eligible="yes",
            model="test",
            prompt_hash="existing",
            resume_hash="existing",
        )
        assert not await store.save_real_job_stage_a(
            real_id,
            result,
            expected_revision=first.input_revision,
            expected_generation=first.claim_generation,
        )
        assert await store.save_real_job_stage_a(
            real_id,
            result,
            expected_revision=second.input_revision,
            expected_generation=second.claim_generation,
        )
        async with aiosqlite.connect(path) as db:
            assert await (
                await db.execute(
                    "SELECT input_revision,reason FROM real_job_evaluation_history "
                    "WHERE real_job_id=?",
                    (int(real_id),),
                )
            ).fetchone() == (first.input_revision, "input_changed")
    finally:
        await store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["jd", "date", "location"])
async def test_source_writer_preserves_completed_score_before_next_run(
    tmp_path: Path,
    change: str,
) -> None:
    path = tmp_path / "invalidate.db"
    store = SQLiteStore(path)
    await store.connect()
    try:
        source = JobPosting(
            platform="speedyapply",
            canonical_id="changed",
            url="https://example.test/changed",
            title="Engineer",
            company="Acme",
            location="Remote",
            discovered_at=datetime.now(UTC),
            posted_at=datetime.now(UTC) - timedelta(days=3),
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
        changes = {
            "jd": {"jd_text": "Build Python services and APIs. " * 15},
            "date": {"posted_at": source.discovered_at + timedelta(hours=3)},
            "location": {"location": "New York"},
        }
        await store.save_job(replace(source, **changes[change]))
        stored = await store.get_job(saved.job_id)
        assert stored.posted_at == source.posted_at

        assert await store.claim_real_job_stage_a_by_ids([real_id]) == []
        async with aiosqlite.connect(path) as db:
            row = await (
                await db.execute(
                    "SELECT stage_a_score,stage_a_status FROM real_job_evaluations "
                    "WHERE real_job_id=?",
                    (int(real_id),),
                )
            ).fetchone()
            assert row == (90, "completed")
            assert (
                await (
                    await db.execute("SELECT COUNT(*) FROM real_job_evaluation_history")
                ).fetchone()
            )[0] == 0
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_canonical_activation_requires_marker_and_complete_parent_state(
    tmp_path: Path,
) -> None:
    store = SQLiteStore(tmp_path / "activation.db")
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
        async with store._lifecycle.connection() as db:
            await db.execute(
                "UPDATE jobs SET real_job_id=NULL WHERE id=?", (int(saved.job_id),)
            )
        with pytest.raises(ValueError, match="orphan"):
            await store.canonical_evaluation_ready()
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_source_evaluation_backfill_reuses_existing_paid_score(
    tmp_path: Path,
) -> None:
    path = tmp_path / "backfill.db"
    store = SQLiteStore(path)
    await store.connect()
    try:
        saved = await store.save_job(
            JobPosting(
                platform="linkedin",
                canonical_id="legacy-score",
                url="https://example.test/legacy",
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
        async with aiosqlite.connect(path) as db:
            assert (
                await (
                    await db.execute(
                        "SELECT stage_a_score FROM real_job_evaluations "
                        "WHERE real_job_id=?",
                        (int(real_id),),
                    )
                ).fetchone()
            )[0] == BACKFILL_SCORE
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_format_only_source_update_keeps_paid_score(tmp_path: Path) -> None:
    path = tmp_path / "format-only.db"
    store = SQLiteStore(path)
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
        async with aiosqlite.connect(path) as db:
            assert (
                await (
                    await db.execute(
                        "SELECT input_revision,stage_a_score FROM real_job_evaluations "
                        "WHERE real_job_id=?",
                        (int(real_id),),
                    )
                ).fetchone()
            ) == (1, 88)
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_gate_fail_can_be_reconsidered_after_policy_change(
    tmp_path: Path,
) -> None:
    store = SQLiteStore(tmp_path / "gate-policy.db")
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


@pytest.mark.asyncio
async def test_stage_b_heartbeat_prevents_second_worker_reclaim(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = [datetime(2026, 9, 24, tzinfo=UTC)]

    def clock() -> datetime:
        return now[0]

    path = tmp_path / "stage-b-heartbeat.db"
    first = SQLiteStore(path, clock=clock)
    second = SQLiteStore(path, clock=clock)
    await first.connect()
    await second.connect()
    monkeypatch.setattr(canonical_module, "STAGE_B_LEASE_HEARTBEAT_SECONDS", 0.01)
    try:
        saved = await first.save_job(
            JobPosting(
                platform="linkedin",
                canonical_id="heartbeat",
                url="https://example.test/heartbeat",
                title="Engineer",
                company="Acme",
                location="Remote",
                discovered_at=now[0],
                jd_text="Build production software services and APIs. " * 10,
                jd_quality=QualityBand.FULL,
            )
        )
        real_id = await first.resolve_real_job_id(saved.job_id)
        stage_a = (await first.claim_real_job_stage_a_by_ids([real_id]))[0]
        await first.save_real_job_stage_a(
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
        stage_b = (
            await first.claim_real_job_stage_b_by_ids(
                [real_id],
                stage_a_threshold=80,
            )
        )[0]
        async with _maintain_real_job_stage_b_claim(first, stage_b) as active:
            assert active
            now[0] += timedelta(minutes=59)
            await asyncio.sleep(0.04)
            now[0] += timedelta(minutes=59)
            assert (
                await second.claim_real_job_stage_b_by_ids(
                    [real_id],
                    stage_a_threshold=80,
                )
                == []
            )
    finally:
        await second.close()
        await first.close()


@pytest.mark.asyncio
async def test_reclaimed_stage_a_rejects_old_worker_same_input(tmp_path: Path) -> None:
    now = [datetime(2026, 9, 24, tzinfo=UTC)]

    def clock() -> datetime:
        return now[0]

    store = SQLiteStore(tmp_path / "claim-generation.db", clock=clock)
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
                discovered_at=now[0],
                jd_text="Build production software services and APIs. " * 10,
                jd_quality=QualityBand.FULL,
            )
        )
        real_id = await store.resolve_real_job_id(saved.job_id)
        old = (await store.claim_real_job_stage_a_by_ids([real_id]))[0]
        now[0] += timedelta(hours=2)
        fresh = (await store.claim_real_job_stage_a_by_ids([real_id]))[0]
        assert fresh.input_revision == old.input_revision
        assert fresh.claim_generation > old.claim_generation
        assert not await store.refresh_real_job_stage_a_claim(
            real_id,
            expected_revision=old.input_revision,
            expected_generation=old.claim_generation,
        )
        assert await store.refresh_real_job_stage_a_claim(
            real_id,
            expected_revision=fresh.input_revision,
            expected_generation=fresh.claim_generation,
        )
        result = StageAResult(
            score=90,
            one_line="Fit",
            timing_eligible="yes",
            model="test",
            prompt_hash="existing",
            resume_hash="existing",
        )
        assert not await store.save_real_job_stage_a(
            real_id,
            result,
            expected_revision=old.input_revision,
            expected_generation=old.claim_generation,
        )
        assert await store.save_real_job_stage_a(
            real_id,
            result,
            expected_revision=fresh.input_revision,
            expected_generation=fresh.claim_generation,
        )
    finally:
        await store.close()


@pytest.mark.asyncio
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
async def test_legacy_score_without_verified_input_is_held_not_reused(
    tmp_path: Path,
    case: str,
    expected_hold: str,
) -> None:
    path = tmp_path / f"hold-{case}.db"
    store = SQLiteStore(path)
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
            async with aiosqlite.connect(path) as db:
                await db.execute(
                    "UPDATE jobs SET enriched_at=? WHERE id=?",
                    ((now + timedelta(days=1)).isoformat(), int(first.job_id)),
                )
                await db.commit()
        if case == "unknown_input_time":
            async with aiosqlite.connect(path) as db:
                await db.execute(
                    "UPDATE jobs SET enriched_at=NULL WHERE id=?",
                    (int(first.job_id),),
                )
                await db.commit()
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
        async with aiosqlite.connect(path) as db:
            state = (
                await (
                    await db.execute(
                        "SELECT identity_review_state FROM real_jobs WHERE id=?",
                        (int(real_id),),
                    )
                ).fetchone()
            )[0]
            current_count = (
                await (
                    await db.execute(
                        "SELECT COUNT(*) FROM real_job_evaluations WHERE real_job_id=?",
                        (int(real_id),),
                    )
                ).fetchone()
            )[0]
            audit_count = (
                await (
                    await db.execute(
                        "SELECT COUNT(*) FROM evaluations e "
                        "JOIN jobs j ON j.id=e.job_id "
                        "WHERE j.real_job_id=? AND e.stage_a_status='completed'",
                        (int(real_id),),
                    )
                ).fetchone()
            )[0]
        assert state == expected_hold
        assert current_count == 0
        assert audit_count == (2 if case == "different_scores" else 1)
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_stage_b_does_not_claim_an_existing_score_placed_on_hold(
    tmp_path: Path,
) -> None:
    path = tmp_path / "stage-b-hold.db"
    store = SQLiteStore(path)
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
        async with aiosqlite.connect(path) as db:
            await db.execute(
                "UPDATE real_jobs SET "
                "identity_review_state='evaluation_input_conflict' "
                "WHERE id=?",
                (int(real_id),),
            )
            await db.commit()
        assert (
            await store.claim_real_job_stage_b_by_ids([real_id], stage_a_threshold=80)
            == []
        )
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_corrected_source_jd_clears_requirements_hold(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "resolved-requirements.db")
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


@pytest.mark.asyncio
async def test_reviewed_source_override_survives_divergent_source_refresh(
    tmp_path: Path,
) -> None:
    path = tmp_path / "reviewed-source.db"
    store = SQLiteStore(path)
    await store.connect()
    try:
        now = datetime.now(UTC)
        ats = "https://boards.greenhouse.io/acme/jobs/1234567"
        official = JobPosting(
            platform="ats",
            canonical_id="official",
            url=ats,
            title="Engineer",
            company="Acme",
            location="Remote",
            discovered_at=now,
            jd_text="Build production Java services and APIs. " * 20,
            jd_quality=QualityBand.FULL,
        )
        summary = JobPosting(
            platform="jobright",
            canonical_id="summary",
            url=ats,
            title="Engineer",
            company="Acme",
            location="Remote",
            discovered_at=now,
            jd_text="Build production Python services and APIs. " * 10,
            jd_quality=QualityBand.FULL,
        )
        official_saved = await store.save_job(official)
        await store.save_job(summary)
        real_id = await store.resolve_real_job_id(official_saved.job_id)
        async with aiosqlite.connect(path) as db:
            await db.execute(
                "INSERT INTO state(key,value) VALUES(?,?)",
                (
                    f"real-job-evaluation-source:{real_id}",
                    str(official_saved.job_id),
                ),
            )
            await db.execute(
                "UPDATE real_jobs SET identity_review_state='clear' WHERE id=?",
                (int(real_id),),
            )
            await db.commit()

        claim = (await store.claim_real_job_stage_a_by_ids([real_id]))[0]
        assert claim.source_job_id == str(official_saved.job_id)

        await store.save_job(replace(summary, jd_text=summary.jd_text + " Updated."))
        detail = await store.get_real_job_view(real_id)
        assert detail is not None
        assert detail["row"]["identity_review_state"] == "clear"
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_policy_change_preserves_completed_stages_sqlite(
    tmp_path: Path,
) -> None:
    path = tmp_path / "policy-change.db"
    store = SQLiteStore(path)
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
                limit=10, stage="a", stage_a_policy=a1
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


@pytest.mark.asyncio
async def test_existing_sqlite_history_gains_policy_column_without_losing_rows(
    tmp_path: Path,
) -> None:
    path = tmp_path / "old-evaluation-history.db"
    store = SQLiteStore(path)
    await store.connect()
    try:
        saved = await store.save_job(
            JobPosting(
                platform="linkedin",
                canonical_id="old-history",
                url="https://example.test/old-history",
                title="Engineer",
                company="Acme",
                location="Remote",
                discovered_at=datetime.now(UTC),
                jd_text="Build software services. " * 12,
                jd_quality=QualityBand.FULL,
            )
        )
        real_id = int(await store.resolve_real_job_id(saved.job_id))
    finally:
        await store.close()
    async with aiosqlite.connect(path) as db:
        await db.execute(
            "ALTER TABLE real_job_evaluation_history DROP COLUMN input_facts_json"
        )
        await db.execute(
            "INSERT INTO real_job_evaluation_history("
            "real_job_id,source_job_id,input_revision,stage_a_status,"
            "stage_a_score,archived_at,reason) "
            "VALUES(?,?,1,'completed',88,?,'old_policy')",
            (real_id, int(saved.job_id), datetime.now(UTC).isoformat()),
        )
        await db.commit()
        await migrate_real_jobs_schema(db)
        row = await (
            await db.execute(
                "SELECT stage_a_score,reason,input_facts_json "
                "FROM real_job_evaluation_history WHERE real_job_id=?",
                (real_id,),
            )
        ).fetchone()
        assert row == (88, "old_policy", None)
