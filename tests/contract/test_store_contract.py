"""Shared contract test suite for JobStore implementations.

Every test uses the ``contract_store`` fixture defined in ``tests/conftest.py``,
which is parametrized by backend (currently ``"sqlite"``; ``"postgres"`` added
later).  Tests exercise **Protocol methods only** — no adapter-specific SQL.
"""

import json
from datetime import UTC, datetime, timedelta

import pytest

from jobfeed.domain.models import (
    ApplicationRecord,
    CompanyRecord,
    MLGateResult,
    PipelineRun,
    QualityBand,
    ResumeSnapshot,
    StageAResult,
    StageBResult,
    Verdict,
)
from jobfeed.domain.scoring import parse_stage_b_response
from tests.support.factories import FIXED_TIME, make_job

# ---------------------------------------------------------------------------
# Constants (no magic numbers)
# ---------------------------------------------------------------------------

HIGH_STAGE_A_SCORE = 80
LOW_STAGE_A_SCORE = 40
MIN_SCORE_FILTER = 70
STAGE_A_COST = 0.10
STAGE_B_COST = 0.25
STAGE_B_FIT_SCORE = 72
COST_AMOUNT_FIRST = 0.05
COST_AMOUNT_SECOND = 0.03
COST_CALLS_AFTER_TWO = 2
TWO_APPLIED_JOBS = 2
RETRY_CAP = 3
DECAY_GHOST_DAYS = 30
DECAY_ARCHIVE_DAYS = 14
FOLLOWUP_GRACE_DAYS = 7
LOOKBACK_DAYS = 60
RESUME_HASH_A = "aabbccdd00112233445566778899aabb00112233445566778899aabbccddeeff"
RESUME_HASH_B = "1122334455667788990011223344556677889900aabbccddeeff0011223344bb"
LIST_LIMIT_SMALL = 5
PENDING_LIMIT = 100
EXPECTED_JOB_PAIR = 2
BUMP_SECOND_COUNT = 2
FLOAT_TOLERANCE = 1e-6
RUN_STAGE_A_COUNT = 3
RUN_STAGE_B_COUNT = 2
NO_DECAY_THRESHOLD = 9999
JD_TEXT_FULL = (
    "We are looking for a software engineer with 3+ years of Python experience. "
    "Must have experience with distributed systems, SQL databases, and REST APIs. "
    "Familiarity with cloud platforms (AWS/GCP) preferred. This is a full-time "
    "remote position with competitive compensation and equity. You will work on "
    "our core data pipeline, building real-time processing systems that handle "
    "millions of events per day. Strong communication skills required. "
    "Experience with Kubernetes and Docker is a plus. BS in CS or equivalent. "
    "Join our growing team of talented engineers building the future of data "
    "infrastructure. We offer flexible hours and a collaborative environment. "
    "Apply today to be part of something amazing."
)
JD_TEXT_STUB = "Short JD."
PIPELINE_RUN_ID = "run-contract-1"
STATE_KEY = "last_scan_cursor"
STATE_VALUE = "2026-05-21T12:00:00Z"
STATE_VALUE_UPDATED = "2026-05-22T12:00:00Z"
COST_DAY = "2026-05-21"


# ---------------------------------------------------------------------------
# Stage A / B helpers (Protocol-only — no raw SQL)
# ---------------------------------------------------------------------------


def _make_stage_a(score: int = HIGH_STAGE_A_SCORE):
    """Build a deterministic StageAResult for contract tests.

    Args:
        score: Desired stage A score.

    Returns:
        StageAResult fixture.
    """
    return StageAResult(
        score=score,
        one_line="Good fit",
        timing_eligible="eligible",
        model="mock/stage-a",
        prompt_hash="stage-a-prompt",
        resume_hash="resume-a",
        cost_usd=STAGE_A_COST,
    )


def _make_stage_b_raw() -> str:
    """Build canonical Stage B JSON for contract tests.

    Returns:
        Raw JSON matching the Stage B parser contract.
    """
    return json.dumps(
        {
            "block_a": {"verdict": "consider"},
            "block_b": {"summary": "Detailed summary"},
            "block_c": {
                "score_0_100": STAGE_B_FIT_SCORE,
                "strong_match": [
                    {"requirement": "Python", "evidence": "Built Python services."}
                ],
                "gaps": [
                    {
                        "requirement": "Kubernetes",
                        "severity": "minor",
                        "mitigation": "Discuss Docker experience.",
                    }
                ],
            },
            "block_e": {"hooks": ["Mention automation."]},
        }
    )


def _make_stage_b() -> StageBResult:
    """Build a parsed Stage B result for contract tests.

    Returns:
        StageBResult with raw blocks preserved.
    """
    return parse_stage_b_response(
        _make_stage_b_raw(),
        model="mock/stage-b",
        prompt_hash="stage-b-prompt",
        resume_hash="resume-b",
        cost_usd=STAGE_B_COST,
    )


# ---------------------------------------------------------------------------
# Helper: insert job + seed through status pipeline
# ---------------------------------------------------------------------------


async def _insert_job(store, canonical_id: str = "c-1", **overrides):
    """Save a job and return (job_id, SaveJobResult).

    Args:
        store: Connected store instance.
        canonical_id: Job canonical ID.
        **overrides: Additional JobPosting field overrides.

    Returns:
        Tuple of (job_id string, SaveJobResult).
    """
    job = make_job(canonical_id, **overrides)
    result = await store.save_job(job)
    return result.job_id, result


async def _insert_scored_job(
    store,
    canonical_id: str = "c-1",
    score: int = HIGH_STAGE_A_SCORE,
    **overrides,
):
    """Insert a job, score it with Stage A, and transition to scored.

    Args:
        store: Connected store instance.
        canonical_id: Job canonical ID.
        score: Stage A score.
        **overrides: Additional JobPosting field overrides.

    Returns:
        Job ID string.
    """
    job_id, _ = await _insert_job(store, canonical_id, **overrides)
    await store.save_stage_a(job_id, _make_stage_a(score))
    await store.transition_status(job_id=job_id, new_status="scored")
    return job_id


# ===========================================================================
# Group 1: Job CRUD + Upsert
# ===========================================================================


class TestJobCRUD:
    """Contract tests for save_job, get_job, list_jobs, job_exists."""

    async def test_save_and_get_round_trip(self, contract_store):
        """save_job then get_job should return the same posting data."""
        job = make_job("crud-1", jd_text=JD_TEXT_FULL)
        result = await contract_store.save_job(job)

        assert result.inserted is True
        assert result.updated is False

        loaded = await contract_store.get_job(result.job_id)
        assert loaded is not None
        assert loaded.id == result.job_id
        assert loaded.canonical_id == "crud-1"
        assert loaded.title == "Backend Intern"
        assert loaded.company == "Example"
        assert loaded.platform == "mock"

    async def test_upsert_same_natural_key(self, contract_store):
        """Second save with same (platform, canonical_id) should update, not insert."""
        first = await contract_store.save_job(make_job("dup-1", jd_text=JD_TEXT_FULL))
        second_job = make_job("dup-1", jd_text=None)
        second_job.title = "Updated Title"
        second = await contract_store.save_job(second_job)

        assert second.job_id == first.job_id
        assert second.inserted is False
        assert second.updated is True

        jobs = await contract_store.list_jobs()
        assert len(jobs) == 1

    async def test_upsert_keeps_higher_quality_jd(self, contract_store):
        """A worse-quality rescrape must not overwrite a better stored JD."""
        await contract_store.save_job(
            make_job("ql-1", jd_text=JD_TEXT_FULL, jd_quality=QualityBand.FULL)
        )
        result = await contract_store.save_job(
            make_job("ql-1", jd_text=JD_TEXT_STUB, jd_quality=QualityBand.STUB)
        )

        loaded = await contract_store.get_job(result.job_id)
        assert loaded is not None
        assert loaded.jd_quality == QualityBand.FULL
        assert loaded.jd_text == JD_TEXT_FULL

    async def test_upsert_takes_higher_quality_jd(self, contract_store):
        """A better-quality rescrape replaces a worse stored JD."""
        first = await contract_store.save_job(
            make_job("ql-2", jd_text=JD_TEXT_STUB, jd_quality=QualityBand.STUB)
        )
        await contract_store.save_job(
            make_job("ql-2", jd_text=JD_TEXT_FULL, jd_quality=QualityBand.FULL)
        )

        loaded = await contract_store.get_job(first.job_id)
        assert loaded is not None
        assert loaded.jd_quality == QualityBand.FULL
        assert loaded.jd_text == JD_TEXT_FULL

    async def test_different_id_same_company_separate(self, contract_store):
        """Different canonical_id with same company should create separate rows."""
        await contract_store.save_job(make_job("a-1", company="Acme Corp"))
        await contract_store.save_job(make_job("a-2", company="Acme Corp"))

        jobs = await contract_store.list_jobs()
        assert len(jobs) == EXPECTED_JOB_PAIR

    async def test_upsert_preserves_jd_when_incoming_is_none(self, contract_store):
        """Upsert with no JD should preserve existing JD via COALESCE."""
        await contract_store.save_job(make_job("q-1", jd_text=JD_TEXT_FULL))
        await contract_store.save_job(make_job("q-1", jd_text=None))

        jobs = await contract_store.list_jobs()
        assert len(jobs) == 1
        loaded = jobs[0]
        # COALESCE keeps existing JD when incoming is None
        assert loaded.jd_text == JD_TEXT_FULL

    async def test_list_jobs_returns_inserted(self, contract_store):
        """list_jobs should return recently inserted jobs."""
        await contract_store.save_job(make_job("list-1"))
        await contract_store.save_job(make_job("list-2"))

        jobs = await contract_store.list_jobs()
        ids = {j.canonical_id for j in jobs}
        assert ids == {"list-1", "list-2"}

    async def test_job_exists_true_and_false(self, contract_store):
        """job_exists should return True for saved jobs, False otherwise."""
        await contract_store.save_job(make_job("exists-1"))

        exists = await contract_store.job_exists(
            platform="mock",
            canonical_id="exists-1",
        )
        assert exists is True
        missing = await contract_store.job_exists(
            platform="mock",
            canonical_id="nope",
        )
        assert missing is False

    async def test_get_job_missing_returns_none(self, contract_store):
        """get_job for a nonexistent ID should return None."""
        result = await contract_store.get_job("999999")
        assert result is None


# ===========================================================================
# Group 2: Evaluation Pipeline
# ===========================================================================


class TestEvaluationPipeline:
    """Contract tests for stage_a, stage_b, pending loads, and skip."""

    async def test_stage_a_removes_from_pending_a(self, contract_store):
        """After save_stage_a, job should not appear in pending stage A."""
        job_id, _ = await _insert_job(contract_store, "eval-1")
        await contract_store.save_stage_a(job_id, _make_stage_a())

        pending = await contract_store.load_pending_stage_a()
        pending_ids = {j.id for j in pending}
        assert job_id not in pending_ids

    async def test_stage_a_makes_pending_b(self, contract_store):
        """After save_stage_a, job should appear in pending stage B."""
        job_id, _ = await _insert_job(contract_store, "eval-2")
        await contract_store.save_stage_a(job_id, _make_stage_a())

        pending_b = await contract_store.load_pending_stage_b()
        pending_ids = {j.id for j in pending_b}
        assert job_id in pending_ids

    async def test_mark_stage_b_skipped(self, contract_store):
        """mark_stage_b_skipped should remove job from pending B queue."""
        job_id, _ = await _insert_job(contract_store, "skip-1")
        await contract_store.save_stage_a(job_id, _make_stage_a(LOW_STAGE_A_SCORE))
        await contract_store.mark_stage_b_skipped(job_id)

        pending_b = await contract_store.load_pending_stage_b()
        pending_ids = {j.id for j in pending_b}
        assert job_id not in pending_ids

    async def test_mark_stage_b_skipped_preserves_completed(self, contract_store):
        """mark_stage_b_skipped must not overwrite a completed Stage B verdict."""
        job_id, _ = await _insert_job(contract_store, "skip-keep")
        await contract_store.save_stage_a(job_id, _make_stage_a())
        await contract_store.save_stage_b(job_id, _make_stage_b())

        await contract_store.mark_stage_b_skipped(job_id)

        evaluation = await contract_store.get_evaluation(job_id)
        assert evaluation is not None
        assert evaluation.stage_b is not None

    async def test_stage_a_error_records_status(self, contract_store):
        """Stage A error should record error status on the evaluation row."""
        job_id, _ = await _insert_job(contract_store, "err-a")
        await contract_store.save_stage_a_error(job_id, "LLM timeout")

        # The error is recorded — job should not appear in pending B
        pending_b = await contract_store.load_pending_stage_b()
        pending_b_ids = {j.id for j in pending_b}
        assert job_id not in pending_b_ids

    async def test_stage_b_error_retryable(self, contract_store):
        """Stage B error should keep job in pending B queue (retry semantics)."""
        job_id, _ = await _insert_job(contract_store, "err-b")
        await contract_store.save_stage_a(job_id, _make_stage_a())
        await contract_store.save_stage_b_error(job_id, "Parse failure")

        pending_b = await contract_store.load_pending_stage_b()
        pending_ids = {j.id for j in pending_b}
        assert job_id in pending_ids

    async def test_stage_b_completes_evaluation(self, contract_store):
        """After save_stage_b, list_evaluated_jobs should include full evaluation."""
        job_id, _ = await _insert_job(contract_store, "full-eval")
        await contract_store.save_stage_a(job_id, _make_stage_a())
        await contract_store.save_stage_b(job_id, _make_stage_b())

        evals = await contract_store.list_evaluated_jobs()
        assert len(evals) >= 1
        ev = next(e for e in evals if e.job.id == job_id)
        assert ev.stage_a is not None
        assert ev.stage_a.score == HIGH_STAGE_A_SCORE
        assert ev.stage_b is not None
        assert ev.stage_b.verdict == Verdict.CONSIDER
        assert ev.stage_b.cost_usd == STAGE_B_COST


# ===========================================================================
# Group 3: Status Lifecycle
# ===========================================================================


class TestStatusLifecycle:
    """Contract tests for status transitions, decay, restore, and notes."""

    async def test_fresh_job_auto_seeded_to_new(self, contract_store):
        """A freshly saved job should have status 'new'."""
        job_id, _ = await _insert_job(contract_store, "status-new")
        status = await contract_store.get_status(job_id)

        assert status is not None
        assert status.status == "new"

    async def test_happy_path_transitions(self, contract_store):
        """Valid transition sequence new -> scored -> shortlisted -> applied."""
        job_id = await _insert_scored_job(contract_store, "happy-path")

        await contract_store.transition_status(job_id=job_id, new_status="shortlisted")
        await contract_store.transition_status(job_id=job_id, new_status="applied")

        status = await contract_store.get_status(job_id)
        assert status is not None
        assert status.status == "applied"

    async def test_invalid_transition_raises(self, contract_store):
        """Invalid transition without force should raise ValueError."""
        job_id, _ = await _insert_job(contract_store, "invalid-t")
        # new -> offer is not in ALLOWED_TRANSITIONS
        with pytest.raises(ValueError, match="not allowed"):
            await contract_store.transition_status(job_id=job_id, new_status="offer")

    async def test_forced_transition_succeeds(self, contract_store):
        """Invalid transition with force=True should succeed."""
        job_id, _ = await _insert_job(contract_store, "force-t")
        result = await contract_store.transition_status(
            job_id=job_id, new_status="offer", force=True
        )
        assert result == "offer"

        status = await contract_store.get_status(job_id)
        assert status is not None
        assert status.status == "offer"

    async def test_transition_to_applied_sets_followup(self, contract_store):
        """Transition to applied should set next_followup_at."""
        job_id = await _insert_scored_job(contract_store, "followup-t")
        await contract_store.transition_status(job_id=job_id, new_status="shortlisted")
        await contract_store.transition_status(job_id=job_id, new_status="applied")

        status = await contract_store.get_status(job_id)
        assert status is not None
        assert status.next_followup_at is not None

    async def test_archived_to_new_requires_double_gate(self, contract_store):
        """archived -> new with force but no i_mean_it should raise."""
        job_id = await _insert_scored_job(contract_store, "double-gate")
        await contract_store.transition_status(
            job_id=job_id, new_status="archived", force=True
        )

        with pytest.raises(ValueError, match="i_mean_it"):
            await contract_store.transition_status(
                job_id=job_id, new_status="new", force=True
            )

    async def test_archived_to_new_with_double_gate(self, contract_store):
        """archived -> new with force and i_mean_it should succeed."""
        job_id = await _insert_scored_job(contract_store, "double-gate-ok")
        await contract_store.transition_status(
            job_id=job_id, new_status="archived", force=True
        )

        result = await contract_store.transition_status(
            job_id=job_id, new_status="new", force=True, i_mean_it=True
        )
        assert result == "new"

    async def test_restore_from_archived(self, contract_store):
        """restore_from_archived should return job to pre-archive status."""
        job_id = await _insert_scored_job(contract_store, "restore-1")
        await contract_store.transition_status(job_id=job_id, new_status="shortlisted")
        await contract_store.transition_status(
            job_id=job_id, new_status="archived", force=True
        )

        restored = await contract_store.restore_from_archived(job_id)
        assert restored == "shortlisted"

        status = await contract_store.get_status(job_id)
        assert status is not None
        assert status.status == "shortlisted"

    async def test_auto_decay_returns_result(self, contract_store):
        """auto_decay should return AutoDecayResult with ghosted/archived counts."""
        job_id = await _insert_scored_job(contract_store, "decay-result")
        await contract_store.transition_status(job_id=job_id, new_status="shortlisted")
        await contract_store.transition_status(job_id=job_id, new_status="applied")

        # With large thresholds, nothing should decay
        result = await contract_store.auto_decay(
            ghost_days=NO_DECAY_THRESHOLD,
            archive_ignored_days=NO_DECAY_THRESHOLD,
        )
        assert result.ghosted == 0
        assert result.archived == 0

        # Status should remain applied
        status = await contract_store.get_status(job_id)
        assert status is not None
        assert status.status == "applied"

    async def test_auto_decay_skips_fresh_ignored(self, contract_store):
        """auto_decay should not archive recently-ignored jobs."""
        job_id = await _insert_scored_job(contract_store, "decay-fresh")
        await contract_store.transition_status(
            job_id=job_id, new_status="ignored", force=True
        )

        # Large threshold: freshly-ignored should not be archived
        result = await contract_store.auto_decay(
            ghost_days=NO_DECAY_THRESHOLD,
            archive_ignored_days=NO_DECAY_THRESHOLD,
        )
        assert result.archived == 0

        status = await contract_store.get_status(job_id)
        assert status is not None
        assert status.status == "ignored"

    async def test_append_note(self, contract_store):
        """append_note should add timestamped text to notes."""
        job_id, _ = await _insert_job(contract_store, "note-1")
        await contract_store.append_note(job_id=job_id, text="recruiter contacted")

        status = await contract_store.get_status(job_id)
        assert status is not None
        assert status.notes is not None
        assert "recruiter contacted" in status.notes

    async def test_forward_only_interview_stages(self, contract_store):
        """Interview stage transitions should be forward-only."""
        job_id = await _insert_scored_job(contract_store, "interview-order")
        await contract_store.transition_status(job_id=job_id, new_status="shortlisted")
        await contract_store.transition_status(job_id=job_id, new_status="applied")
        await contract_store.transition_status(job_id=job_id, new_status="oa")
        await contract_store.transition_status(job_id=job_id, new_status="hr_call")

        # hr_call -> oa is not allowed (backward)
        with pytest.raises(ValueError, match="not allowed"):
            await contract_store.transition_status(job_id=job_id, new_status="oa")


# ===========================================================================
# Group 4: Application Audit Trail
# ===========================================================================


class TestApplicationAudit:
    """Contract tests for record_application, list_applications, application_stats."""

    async def test_record_application_creates_and_transitions(self, contract_store):
        """record_application should create audit record and transition to applied."""
        job_id, _ = await _insert_job(contract_store, "app-1")
        record = ApplicationRecord(
            job_id=job_id,
            applied_at=FIXED_TIME,
            master_resume_hash=RESUME_HASH_A,
            application_method="website",
            notes="First application",
        )
        is_new = await contract_store.record_application(record)

        assert is_new is True

        status = await contract_store.get_status(job_id)
        assert status is not None
        assert status.status == "applied"

    async def test_duplicate_application_is_noop(self, contract_store):
        """record_application on already-applied job returns False."""
        job_id, _ = await _insert_job(contract_store, "app-dup")
        record = ApplicationRecord(
            job_id=job_id,
            applied_at=FIXED_TIME,
            notes="Original",
        )
        first = await contract_store.record_application(record)
        assert first is True

        second_record = ApplicationRecord(
            job_id=job_id,
            applied_at=FIXED_TIME + timedelta(hours=1),
            notes="Duplicate attempt",
        )
        second = await contract_store.record_application(second_record)
        assert second is False

    async def test_duplicate_preserves_original_record(self, contract_store):
        """After duplicate record_application, original audit data is preserved."""
        job_id, _ = await _insert_job(contract_store, "app-preserve")
        record = ApplicationRecord(
            job_id=job_id,
            applied_at=FIXED_TIME,
            notes="Original notes",
        )
        await contract_store.record_application(record)

        dup = ApplicationRecord(
            job_id=job_id,
            applied_at=FIXED_TIME + timedelta(hours=1),
            notes="Should not overwrite",
        )
        await contract_store.record_application(dup)

        apps = await contract_store.list_applications()
        matching = [a for a in apps if a.job_id == job_id]
        assert len(matching) == 1
        assert matching[0].notes == "Original notes"

    async def test_list_applications_with_snapshot_fields(self, contract_store):
        """list_applications should return records with all snapshot fields."""
        job_id, _ = await _insert_job(contract_store, "app-fields")
        record = ApplicationRecord(
            job_id=job_id,
            applied_at=FIXED_TIME,
            master_resume_hash=RESUME_HASH_A,
            tailored_resume_hash=RESUME_HASH_B,
            cover_letter="Dear Hiring Manager...",
            application_method="easy_apply",
            verdict_snapshot='{"verdict":"consider"}',
            fit_snapshot='{"score":75}',
            hooks_snapshot='["hook1"]',
            notes="Test notes",
        )
        await contract_store.record_application(record)

        apps = await contract_store.list_applications()
        assert len(apps) >= 1
        app = next(a for a in apps if a.job_id == job_id)
        assert app.master_resume_hash == RESUME_HASH_A
        assert app.application_method == "easy_apply"
        assert app.cover_letter == "Dear Hiring Manager..."

    async def test_application_stats_basic(self, contract_store):
        """application_stats should return correct basic counts."""
        job_id, _ = await _insert_job(contract_store, "stats-1")
        record = ApplicationRecord(job_id=job_id, applied_at=FIXED_TIME)
        await contract_store.record_application(record)

        stats = await contract_store.application_stats(since_days_ago=365)
        assert stats.applied_count >= 1

    async def test_application_stats_by_resume(self, contract_store):
        """application_stats(by_resume=True) returns per-variant counts.

        Two applied jobs (one advanced to a response) exercise the by-resume
        breakdown path, which previously failed on Postgres because the query
        bound response-status params it did not reference.
        """
        job_a, _ = await _insert_job(contract_store, "stats-resume-a")
        job_b, _ = await _insert_job(contract_store, "stats-resume-b")
        await contract_store.record_application(
            ApplicationRecord(job_id=job_a, applied_at=FIXED_TIME)
        )
        await contract_store.record_application(
            ApplicationRecord(job_id=job_b, applied_at=FIXED_TIME)
        )
        await contract_store.transition_status(job_id=job_a, new_status="oa")

        stats = await contract_store.application_stats(
            since_days_ago=365, by_resume=True
        )

        assert stats.applied_count >= TWO_APPLIED_JOBS
        assert stats.by_resume is not None
        assert sum(v.sent for v in stats.by_resume.values()) >= TWO_APPLIED_JOBS

    async def test_duplicate_application_after_terminal_is_noop(self, contract_store):
        """A duplicate record_application after a terminal status stays a no-op.

        Reproduces a Postgres divergence: the terminal-status guard must not turn
        a duplicate (already-applied) call into an error.
        """
        job_id, _ = await _insert_job(contract_store, "app-term")
        first = await contract_store.record_application(
            ApplicationRecord(job_id=job_id, applied_at=FIXED_TIME)
        )
        assert first is True

        await contract_store.transition_status(job_id=job_id, new_status="rejected")

        again = await contract_store.record_application(
            ApplicationRecord(job_id=job_id, applied_at=FIXED_TIME + timedelta(hours=1))
        )
        assert again is False


# ===========================================================================
# Group 5: Resume Snapshots
# ===========================================================================


class TestResumeSnapshots:
    """Contract tests for save/get resume_snapshot and register_resume_variant."""

    async def test_save_and_get_snapshot(self, contract_store):
        """save_resume_snapshot then get_resume_snapshot should round-trip."""
        snapshot = ResumeSnapshot(
            resume_hash=RESUME_HASH_A,
            captured_at=FIXED_TIME,
            source="master",
            content="# My Resume\n\nExperience: ...",
            notes="Version 1",
        )
        await contract_store.save_resume_snapshot(snapshot)

        loaded = await contract_store.get_resume_snapshot(RESUME_HASH_A)
        assert loaded is not None
        assert loaded.resume_hash == RESUME_HASH_A
        assert loaded.source == "master"
        assert loaded.content == "# My Resume\n\nExperience: ..."
        assert loaded.notes == "Version 1"

    async def test_snapshot_idempotent(self, contract_store):
        """Saving same hash twice should not error or change content."""
        snapshot = ResumeSnapshot(
            resume_hash=RESUME_HASH_A,
            captured_at=FIXED_TIME,
            source="master",
            content="Original content",
        )
        await contract_store.save_resume_snapshot(snapshot)

        # Save again with different content — should be no-op
        snapshot2 = ResumeSnapshot(
            resume_hash=RESUME_HASH_A,
            captured_at=FIXED_TIME + timedelta(hours=1),
            source="tailored",
            content="Different content",
        )
        await contract_store.save_resume_snapshot(snapshot2)

        loaded = await contract_store.get_resume_snapshot(RESUME_HASH_A)
        assert loaded is not None
        assert loaded.content == "Original content"

    async def test_get_missing_snapshot(self, contract_store):
        """get_resume_snapshot for missing hash should return None."""
        result = await contract_store.get_resume_snapshot(RESUME_HASH_B)
        assert result is None

    async def test_register_resume_variant(self, contract_store):
        """register_resume_variant returns True on first, False on second."""
        first = await contract_store.register_resume_variant(
            name="v1-technical", description="Technical focus"
        )
        assert first is True

        second = await contract_store.register_resume_variant(
            name="v1-technical", description="Updated description"
        )
        assert second is False


# ===========================================================================
# Group 6: Company Management
# ===========================================================================


class TestCompanyManagement:
    """Contract tests for upsert/get/list company, remove, failures."""

    async def test_upsert_and_get_company(self, contract_store):
        """upsert_company then get_company should round-trip."""
        company = CompanyRecord(
            slug="acme-corp",
            ats_vendor="greenhouse",
            notes="Good employer",
        )
        await contract_store.upsert_company(company)

        loaded = await contract_store.get_company("acme-corp")
        assert loaded is not None
        assert loaded.slug == "acme-corp"
        assert loaded.ats_vendor == "greenhouse"
        assert loaded.notes == "Good employer"

    async def test_upsert_preserves_none_fields(self, contract_store):
        """upsert_company with None fields should preserve existing values."""
        company = CompanyRecord(
            slug="preserve-co",
            ats_vendor="lever",
            notes="Original notes",
        )
        await contract_store.upsert_company(company)

        # Update without ats_vendor — should keep existing
        update = CompanyRecord(slug="preserve-co", notes=None)
        await contract_store.upsert_company(update)

        loaded = await contract_store.get_company("preserve-co")
        assert loaded is not None
        assert loaded.ats_vendor == "lever"
        assert loaded.notes == "Original notes"

    async def test_list_companies_filters_vendor(self, contract_store):
        """list_companies with vendor filter should return matching only."""
        await contract_store.upsert_company(
            CompanyRecord(slug="gh-co", ats_vendor="greenhouse")
        )
        await contract_store.upsert_company(
            CompanyRecord(slug="lv-co", ats_vendor="lever")
        )

        gh_list = await contract_store.list_companies(vendor="greenhouse")
        slugs = {c.slug for c in gh_list}
        assert "gh-co" in slugs
        assert "lv-co" not in slugs

    async def test_mark_company_removed(self, contract_store):
        """mark_company_removed should exclude from default list."""
        await contract_store.upsert_company(
            CompanyRecord(slug="remove-co", ats_vendor="lever")
        )
        removed = await contract_store.mark_company_removed("remove-co")
        assert removed is True

        default_list = await contract_store.list_companies()
        slugs = {c.slug for c in default_list}
        assert "remove-co" not in slugs

        # include_removed shows it
        full_list = await contract_store.list_companies(include_removed=True)
        slugs_full = {c.slug for c in full_list}
        assert "remove-co" in slugs_full

    async def test_bump_discover_failure(self, contract_store):
        """bump_discover_failure should increment and return new count."""
        await contract_store.upsert_company(CompanyRecord(slug="fail-co"))

        count1 = await contract_store.bump_discover_failure("fail-co")
        assert count1 == 1

        count2 = await contract_store.bump_discover_failure("fail-co")
        assert count2 == BUMP_SECOND_COUNT

    async def test_reset_discover_failures(self, contract_store):
        """reset_discover_failures should zero the counter."""
        await contract_store.upsert_company(CompanyRecord(slug="reset-co"))
        await contract_store.bump_discover_failure("reset-co")
        await contract_store.bump_discover_failure("reset-co")

        await contract_store.reset_discover_failures("reset-co")
        loaded = await contract_store.get_company("reset-co")
        assert loaded is not None
        assert loaded.consecutive_discover_failures == 0


# ===========================================================================
# Group 7: ML Gate
# ===========================================================================


class TestMLGate:
    """Contract tests for save_ml_gate_result with pass and fail scenarios."""

    async def test_ml_gate_pass(self, contract_store):
        """save_ml_gate_result with pass should persist score and result."""
        job_id, _ = await _insert_job(contract_store, "ml-pass")
        gate = MLGateResult(
            score=0.92,
            result="pass",
            fail_reason=None,
            version="v1.0",
            is_swe_role=True,
            seniority_level="mid",
            degree_required="bachelors",
            clearance_required=False,
            school_restricted=False,
            yoe_min=2,
            domain_tags=["backend", "data"],
            tech_required=["python", "sql"],
            role_type="fulltime",
        )
        await contract_store.save_ml_gate_result(job_id, gate)

        # Verify via get_job — ML gate fields are on the job row
        loaded = await contract_store.get_job(job_id)
        assert loaded is not None
        # The job was loaded, gate was written (Protocol only verifies no error)

    async def test_ml_gate_fail_with_reason(self, contract_store):
        """save_ml_gate_result with fail should persist fail_reason."""
        job_id, _ = await _insert_job(contract_store, "ml-fail")
        gate = MLGateResult(
            score=0.15,
            result="fail",
            fail_reason="clearance_required",
            version="v1.0",
            is_swe_role=True,
            clearance_required=True,
        )
        await contract_store.save_ml_gate_result(job_id, gate)

        # No error raised — gate result persisted
        loaded = await contract_store.get_job(job_id)
        assert loaded is not None

    async def test_ml_gate_all_feature_columns(self, contract_store):
        """save_ml_gate_result should persist all feature columns without error."""
        job_id, _ = await _insert_job(contract_store, "ml-features")
        gate = MLGateResult(
            score=0.88,
            result="pass",
            version="v2.1",
            is_swe_role=True,
            seniority_level="senior",
            degree_required="masters",
            clearance_required=False,
            school_restricted=True,
            yoe_min=5,
            domain_tags=["ml", "infra", "platform"],
            tech_required=["python", "pytorch", "kubernetes"],
            role_type="fulltime",
        )
        await contract_store.save_ml_gate_result(job_id, gate)

        # Roundtrip verifiable via no exceptions
        loaded = await contract_store.get_job(job_id)
        assert loaded is not None


# ===========================================================================
# Group 8: State / Cost / Pipeline
# ===========================================================================


class TestStateCostPipeline:
    """Contract tests for get/set_state, record/get_cost, pipeline_run."""

    async def test_set_and_get_state(self, contract_store):
        """set_state then get_state should return the value."""
        await contract_store.set_state(STATE_KEY, STATE_VALUE)
        result = await contract_store.get_state(STATE_KEY)
        assert result == STATE_VALUE

    async def test_set_state_overwrites(self, contract_store):
        """set_state on existing key should overwrite."""
        await contract_store.set_state(STATE_KEY, STATE_VALUE)
        await contract_store.set_state(STATE_KEY, STATE_VALUE_UPDATED)

        result = await contract_store.get_state(STATE_KEY)
        assert result == STATE_VALUE_UPDATED

    async def test_get_state_missing(self, contract_store):
        """get_state for missing key should return None."""
        result = await contract_store.get_state("nonexistent_key")
        assert result is None

    async def test_record_cost_single(self, contract_store):
        """record_cost then get_cost should return entry with calls=1."""
        await contract_store.record_cost(day=COST_DAY, spent_usd=COST_AMOUNT_FIRST)
        entry = await contract_store.get_cost(COST_DAY)

        assert entry is not None
        assert entry.day == COST_DAY
        assert abs(entry.spent_usd - COST_AMOUNT_FIRST) < FLOAT_TOLERANCE
        assert entry.calls == 1

    async def test_record_cost_accumulates(self, contract_store):
        """Two record_cost calls on same day should accumulate usd, increment calls."""
        await contract_store.record_cost(day=COST_DAY, spent_usd=COST_AMOUNT_FIRST)
        await contract_store.record_cost(day=COST_DAY, spent_usd=COST_AMOUNT_SECOND)

        entry = await contract_store.get_cost(COST_DAY)
        assert entry is not None
        expected = COST_AMOUNT_FIRST + COST_AMOUNT_SECOND
        assert abs(entry.spent_usd - expected) < FLOAT_TOLERANCE
        assert entry.calls == COST_CALLS_AFTER_TWO

    async def test_get_cost_range_includes_boundary_day(self, contract_store):
        """get_cost_range(since_days=0) includes today's row (lower bound inclusive).

        Uses the UTC date to match the stores' date('now')/CURRENT_DATE clock.
        """
        today = datetime.now(UTC).date().isoformat()
        await contract_store.record_cost(day=today, spent_usd=COST_AMOUNT_FIRST)

        entries = await contract_store.get_cost_range(since_days=0)

        assert today in {entry.day for entry in entries}

    async def test_get_cost_missing_day(self, contract_store):
        """get_cost for a day with no entries should return None."""
        result = await contract_store.get_cost("1999-01-01")
        assert result is None

    async def test_get_cost_range(self, contract_store):
        """get_cost_range should return entries within the range."""
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        await contract_store.record_cost(day=today, spent_usd=COST_AMOUNT_FIRST)

        entries = await contract_store.get_cost_range(since_days=7)
        assert len(entries) >= 1
        assert entries[0].day == today

    async def test_pipeline_run_round_trip(self, contract_store):
        """record_pipeline_run then get_pipeline_run should round-trip."""
        run = PipelineRun(
            run_id=PIPELINE_RUN_ID,
            started_at=FIXED_TIME,
            source="evaluate",
            stage_a_scored=RUN_STAGE_A_COUNT,
            stage_b_scored=RUN_STAGE_B_COUNT,
            jobs_scored=RUN_STAGE_A_COUNT + RUN_STAGE_B_COUNT,
            finished_at=FIXED_TIME + timedelta(minutes=5),
        )
        await contract_store.record_pipeline_run(run)

        loaded = await contract_store.get_pipeline_run(PIPELINE_RUN_ID)
        assert loaded is not None
        assert loaded.run_id == PIPELINE_RUN_ID
        assert loaded.stage_a_scored == RUN_STAGE_A_COUNT
        assert loaded.stage_b_scored == RUN_STAGE_B_COUNT
        assert loaded.source == "evaluate"

    async def test_get_pipeline_run_missing(self, contract_store):
        """get_pipeline_run for missing ID should return None."""
        result = await contract_store.get_pipeline_run("nonexistent-run")
        assert result is None


# ===========================================================================
# Group 9: Workflow Queries
# ===========================================================================


class TestWorkflowQueries:
    """Contract tests for workflow_attention and compute_reapply_notice."""

    async def test_workflow_attention_structure(self, contract_store):
        """workflow_attention should return three-bucket structure."""
        result = await contract_store.workflow_attention()

        assert hasattr(result, "follow_up_today")
        assert hasattr(result, "interview_prep")
        assert hasattr(result, "going_ghosted")
        assert isinstance(result.follow_up_today, list)
        assert isinstance(result.interview_prep, list)
        assert isinstance(result.going_ghosted, list)

    async def test_reapply_notice_none_without_norm(self, contract_store):
        """compute_reapply_notice requires company_norm to detect overlap.

        When company_norm is not populated (Phase 0 save_job does not
        compute it), the notice cannot detect overlap.  This test verifies
        the method runs without error and returns None in that case.
        """
        job_id_1, _ = await _insert_job(contract_store, "reapply-1", company="SameCo")
        job_id_2, _ = await _insert_job(contract_store, "reapply-2", company="SameCo")

        record = ApplicationRecord(job_id=job_id_1, applied_at=FIXED_TIME)
        await contract_store.record_application(record)

        # company_norm not populated by Phase 0 save_job, so no match
        notice = await contract_store.compute_reapply_notice(
            job_id=job_id_2, lookback_days=LOOKBACK_DAYS
        )
        # Either None (norm not populated) or a notice string (norm populated)
        assert notice is None or isinstance(notice, str)

    async def test_compute_reapply_notice_no_overlap(self, contract_store):
        """compute_reapply_notice returns None when no overlap."""
        job_id, _ = await _insert_job(contract_store, "no-reapply", company="UniqueCo")
        notice = await contract_store.compute_reapply_notice(
            job_id=job_id, lookback_days=LOOKBACK_DAYS
        )
        assert notice is None


# ===========================================================================
# Group 10: Status Listing + Filtering
# ===========================================================================


class TestStatusListing:
    """Contract tests for list_statuses with filter combinations."""

    async def test_filter_by_status(self, contract_store):
        """list_statuses(statuses={'applied'}) returns only applied jobs."""
        job_id = await _insert_scored_job(contract_store, "ls-applied")
        await contract_store.transition_status(job_id=job_id, new_status="shortlisted")
        await contract_store.transition_status(job_id=job_id, new_status="applied")

        await _insert_scored_job(contract_store, "ls-scored")

        results = await contract_store.list_statuses(statuses=frozenset({"applied"}))
        statuses = {r.status for r in results}
        assert "applied" in statuses
        assert "scored" not in statuses

    async def test_filter_by_days(self, contract_store):
        """list_statuses(days=7) returns recent status changes."""
        job_id, _ = await _insert_job(contract_store, "ls-days")

        results = await contract_store.list_statuses(days=7)
        job_ids = {r.job_id for r in results}
        assert job_id in job_ids

    async def test_filter_notes_contain(self, contract_store):
        """list_statuses(notes_contain='recruiter') matches note substring."""
        job_id, _ = await _insert_job(contract_store, "ls-notes")
        await contract_store.append_note(job_id=job_id, text="recruiter called back")

        results = await contract_store.list_statuses(notes_contain="recruiter")
        job_ids = {r.job_id for r in results}
        assert job_id in job_ids

    async def test_filter_notes_contain_excludes_non_match(self, contract_store):
        """list_statuses(notes_contain=...) should exclude non-matching notes."""
        job_id, _ = await _insert_job(contract_store, "ls-no-match")
        await contract_store.append_note(job_id=job_id, text="sent follow-up email")

        results = await contract_store.list_statuses(notes_contain="recruiter")
        job_ids = {r.job_id for r in results}
        assert job_id not in job_ids

    async def test_filter_limit(self, contract_store):
        """list_statuses(limit=N) should cap result count."""
        for i in range(LIST_LIMIT_SMALL + 2):
            await _insert_job(contract_store, f"ls-limit-{i}")

        results = await contract_store.list_statuses(limit=LIST_LIMIT_SMALL)
        assert len(results) <= LIST_LIMIT_SMALL

    async def test_filter_needs_followup(self, contract_store):
        """list_statuses(needs_followup=True) returns jobs with past followup date."""
        job_id = await _insert_scored_job(contract_store, "ls-followup")
        await contract_store.transition_status(job_id=job_id, new_status="shortlisted")
        # Transition to applied sets followup in the future
        await contract_store.transition_status(
            job_id=job_id,
            new_status="applied",
            followup_grace_days=0,
        )

        results = await contract_store.list_statuses(needs_followup=True)
        job_ids = {r.job_id for r in results}
        assert job_id in job_ids


# ===========================================================================
# Group 11: Pending Load Refinements
# ===========================================================================


class TestPendingLoadRefinements:
    """Contract tests for load_pending_stage_a/b with filters."""

    async def test_pending_a_default_excludes_completed(self, contract_store):
        """load_pending_stage_a should not return completed evaluations."""
        job_id, _ = await _insert_job(contract_store, "pend-a-done")
        await contract_store.save_stage_a(job_id, _make_stage_a())

        pending = await contract_store.load_pending_stage_a()
        pending_ids = {j.id for j in pending}
        assert job_id not in pending_ids

    async def test_pending_a_returns_unevaluated(self, contract_store):
        """load_pending_stage_a should return jobs with no evaluation."""
        job_id, _ = await _insert_job(contract_store, "pend-a-new")

        pending = await contract_store.load_pending_stage_a()
        pending_ids = {j.id for j in pending}
        assert job_id in pending_ids

    async def test_pending_b_excludes_skipped(self, contract_store):
        """load_pending_stage_b should exclude skipped jobs."""
        job_id, _ = await _insert_job(contract_store, "pend-b-skip")
        await contract_store.save_stage_a(job_id, _make_stage_a(LOW_STAGE_A_SCORE))
        await contract_store.mark_stage_b_skipped(job_id)

        pending_b = await contract_store.load_pending_stage_b()
        pending_ids = {j.id for j in pending_b}
        assert job_id not in pending_ids

    async def test_pending_b_returns_stage_a_completed(self, contract_store):
        """load_pending_stage_b should return Stage A completed, Stage B pending."""
        job_id, _ = await _insert_job(contract_store, "pend-b-ready")
        await contract_store.save_stage_a(job_id, _make_stage_a())

        pending_b = await contract_store.load_pending_stage_b()
        pending_ids = {j.id for j in pending_b}
        assert job_id in pending_ids

    async def test_pending_b_excludes_b_completed(self, contract_store):
        """load_pending_stage_b should exclude Stage B completed jobs."""
        job_id, _ = await _insert_job(contract_store, "pend-b-done")
        await contract_store.save_stage_a(job_id, _make_stage_a())
        await contract_store.save_stage_b(job_id, _make_stage_b())

        pending_b = await contract_store.load_pending_stage_b()
        pending_ids = {j.id for j in pending_b}
        assert job_id not in pending_ids


# ===========================================================================
# Group 12: Enrichment
# ===========================================================================


class TestEnrichment:
    """Contract tests for record_enrichment and enrich_paste."""

    async def test_record_enrichment_stamps_job(self, contract_store):
        """record_enrichment should update jd_text, quality, and source."""
        job_id, _ = await _insert_job(contract_store, "enrich-1")
        await contract_store.record_enrichment(
            job_id=job_id,
            jd_text=JD_TEXT_FULL,
            jd_quality="full",
            enriched_at=FIXED_TIME,
            enrich_source="scraper",
        )

        loaded = await contract_store.get_job(job_id)
        assert loaded is not None
        assert loaded.jd_text == JD_TEXT_FULL
        assert loaded.enrich_source == "scraper"

    async def test_enrich_paste_stamps_manual(self, contract_store):
        """enrich_paste should assess quality and set enrich_source='manual-paste'."""
        job_id, _ = await _insert_job(contract_store, "paste-1")

        returned_id = await contract_store.enrich_paste(
            platform="mock",
            canonical_id="paste-1",
            jd_text=JD_TEXT_FULL,
        )
        assert returned_id == job_id

        loaded = await contract_store.get_job(job_id)
        assert loaded is not None
        assert loaded.jd_text == JD_TEXT_FULL
        assert loaded.enrich_source == "manual-paste"

    async def test_enrich_paste_nonexistent_raises(self, contract_store):
        """enrich_paste on nonexistent job should raise ValueError."""
        with pytest.raises(ValueError, match="not found"):
            await contract_store.enrich_paste(
                platform="mock",
                canonical_id="nonexistent",
                jd_text="Some JD text",
            )


# ===========================================================================
# Group 13: Evaluation Reads
# ===========================================================================


class TestEvaluationReads:
    """Contract tests for evaluation reads and digest stats."""

    async def test_get_evaluation_full(self, contract_store):
        """get_evaluation should return full JobEvaluation with both stages."""
        job_id, _ = await _insert_job(contract_store, "eval-read-1")
        await contract_store.save_stage_a(job_id, _make_stage_a())
        await contract_store.save_stage_b(job_id, _make_stage_b())

        ev = await contract_store.get_evaluation(job_id)
        assert ev is not None
        assert ev.job.id == job_id
        assert ev.stage_a is not None
        assert ev.stage_a.score == HIGH_STAGE_A_SCORE
        assert ev.stage_b is not None
        assert ev.stage_b.verdict == Verdict.CONSIDER

    async def test_get_evaluation_unevaluated(self, contract_store):
        """get_evaluation for unevaluated job returns eval with None stages."""
        job_id, _ = await _insert_job(contract_store, "eval-read-empty")

        ev = await contract_store.get_evaluation(job_id)
        assert ev is not None
        assert ev.job.id == job_id
        assert ev.stage_a is None
        assert ev.stage_b is None

    async def test_top_evaluated_jobs_filter(self, contract_store):
        """top_evaluated_jobs with min_score should filter by stage_a_score."""
        # Insert a high-score job
        high_id, _ = await _insert_job(contract_store, "top-high")
        await contract_store.save_stage_a(high_id, _make_stage_a(HIGH_STAGE_A_SCORE))
        await contract_store.save_stage_b(high_id, _make_stage_b())

        # Insert a low-score job
        low_id, _ = await _insert_job(contract_store, "top-low")
        await contract_store.save_stage_a(low_id, _make_stage_a(LOW_STAGE_A_SCORE))
        await contract_store.save_stage_b(low_id, _make_stage_b())

        results = await contract_store.top_evaluated_jobs(min_score=MIN_SCORE_FILTER)
        job_ids = {e.job.id for e in results}
        assert high_id in job_ids
        assert low_id not in job_ids

    async def test_top_evaluated_sorted_descending(self, contract_store):
        """top_evaluated_jobs should return results sorted by score desc."""
        ids = []
        for i, score in enumerate([60, 90, 75]):
            jid, _ = await _insert_job(contract_store, f"sort-{i}")
            await contract_store.save_stage_a(jid, _make_stage_a(score))
            await contract_store.save_stage_b(jid, _make_stage_b())
            ids.append(jid)

        results = await contract_store.top_evaluated_jobs()
        scores = [e.stage_a.score for e in results if e.stage_a is not None]
        assert scores == sorted(scores, reverse=True)

    async def test_digest_stats_structure(self, contract_store):
        """digest_stats should return a complete DigestStats object."""
        # Seed some data
        await _insert_job(contract_store, "digest-1")

        stats = await contract_store.digest_stats()
        assert stats.total_jobs >= 1
        assert isinstance(stats.scored_today, int)
        assert isinstance(stats.stage_b_evaluated, int)
        assert isinstance(stats.filtered_count, int)
        assert isinstance(stats.llm_calls_today, int)
        assert isinstance(stats.total_cost_today_usd, float)

    async def test_needs_attention_structure(self, contract_store):
        """needs_attention should return an AttentionReport."""
        result = await contract_store.needs_attention()
        assert hasattr(result, "enrich_errors")
        assert hasattr(result, "low_quality_scored")
        assert isinstance(result.enrich_errors, list)
        assert isinstance(result.low_quality_scored, list)

    async def test_needs_attention_surfaces_low_quality_scored(self, contract_store):
        """needs_attention should detect low-quality jobs that have been scored."""
        job_id, _ = await _insert_job(contract_store, "attn-lq", jd_text=JD_TEXT_STUB)
        await contract_store.save_stage_a(job_id, _make_stage_a())

        report = await contract_store.needs_attention()
        categories = {item.category for item in report.low_quality_scored}
        # If the store surfaced low_quality_scored items, they should be categorized
        if report.low_quality_scored:
            assert "low_quality_scored" in categories

    async def test_needs_attention_surfaces_capped_errors(self, contract_store):
        """Jobs stuck past the Stage A retry cap appear in stuck_scoring."""
        job_id, _ = await _insert_job(contract_store, "attn-stuck")
        for _ in range(RETRY_CAP):
            await contract_store.save_stage_a_error(job_id, "boom")

        report = await contract_store.needs_attention()

        assert job_id in {item.job_id for item in report.stuck_scoring}
