"""PostgresStore behavioral tests ported from the former SQLite store suite.

These cover store behaviors not exercised by the shared contract suite:
pending-queue filters (corpus / quality / freshness / retry cap), follow-up and
reapply lifecycle, stage timestamp stamping, raw Stage B block persistence, and
schema-level guards (CHECK / FK / auto-seed trigger) on PostgreSQL.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg
import pytest

from jobfeed.adapters.store.postgres import PostgresStore
from jobfeed.domain.models import (
    MLGateResult,
    QualityBand,
    StageAResult,
    StageBResult,
    TransitionRequest,
)
from jobfeed.domain.scoring import parse_stage_b_response
from tests.support.factories import make_job

ML_GATE_COLUMNS = (
    "ml_gate_score",
    "ml_gate_result",
    "ml_gate_fail_reason",
    "ml_gate_at",
    "ml_gate_version",
)

pytestmark = pytest.mark.postgres

HIGH_STAGE_A_SCORE = 80
LOW_STAGE_A_SCORE = 40
STAGE_B_SCORE = 72
STAGE_A_COST = 0.10
STAGE_B_COST = 0.25


def make_store_job(
    canonical_id: str = "mock-1",
    *,
    jd_text: str | None = "Detailed JD",
) -> Any:
    """Create a valid job posting for store tests.

    Args:
        canonical_id: Source-specific natural identity.
        jd_text: Optional JD text to persist.

    Returns:
        Job posting with deterministic timestamps.
    """
    return make_job(
        canonical_id,
        jd_text=jd_text,
        jd_quality=QualityBand.GOOD if jd_text is not None else None,
    )


def make_stage_a(score: int = HIGH_STAGE_A_SCORE) -> StageAResult:
    """Create a Stage A result for store tests.

    Args:
        score: Stage A score to persist.

    Returns:
        Stage A result with deterministic metadata.
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


def make_stage_b_raw() -> str:
    """Create canonical Stage B JSON for persistence tests.

    Returns:
        Raw JSON matching the Stage B parser contract.
    """
    return json.dumps(
        {
            "verdict": {
                "recommendation": "consider",
                "confidence": 3,
                "one_line": "Reasonable fit.",
            },
            "jd_summary": {
                "role_in_3_lines": "Detailed summary",
                "must_haves": ["Python"],
                "nice_to_haves": ["Kubernetes"],
                "red_flags_in_jd": [],
            },
            "fit_analysis": {
                "score_0_100": STAGE_B_SCORE,
                "strong_match": [
                    {
                        "requirement": "Python",
                        "evidence_from_resume": "Built Python services.",
                    }
                ],
                "gaps": [
                    {
                        "requirement": "Kubernetes",
                        "severity": "minor",
                        "mitigation": "Discuss Docker experience.",
                    }
                ],
            },
            "resume_hooks": {
                "lead_with": "Mention automation.",
                "supporting": [],
                "avoid_mentioning": [],
            },
        }
    )


def make_stage_b() -> StageBResult:
    """Create a parsed Stage B result.

    Returns:
        Stage B domain result with raw blocks preserved.
    """
    return parse_stage_b_response(
        make_stage_b_raw(),
        model="mock/stage-b",
        prompt_hash="stage-b-prompt",
        resume_hash="resume-b",
        cost_usd=STAGE_B_COST,
    )


def make_ml_gate(result: str = "fail") -> MLGateResult:
    """Create an ML gate result for seeding a persisted verdict.

    Args:
        result: ``'pass'`` or ``'fail'``.

    Returns:
        Gate result with a deterministic score/version + a few feature fields.
    """
    return MLGateResult(
        score=0.12 if result == "fail" else 0.91,
        result=result,
        fail_reason="low score" if result == "fail" else None,
        version="gate-v1",
        is_swe_role=True,
    )


async def _ml_gate_row(store: PostgresStore, job_id: str) -> dict[str, Any]:
    """Read the ml_gate_* verdict columns for one job.

    Args:
        store: Connected PostgresStore.
        job_id: Store-assigned job identity.

    Returns:
        Mapping of the five ``ml_gate_*`` verdict columns for the row.
    """
    conn = await store._get_pool().acquire()  # type: ignore[attr-defined]
    try:
        row = await conn.fetchrow(
            "SELECT ml_gate_score, ml_gate_result, ml_gate_fail_reason, "
            "ml_gate_at, ml_gate_version FROM jobs WHERE id = $1",
            int(job_id),
        )
    finally:
        await store._get_pool().release(conn)  # type: ignore[attr-defined]
    assert row is not None
    return dict(row)


async def _eval_row(store: PostgresStore, job_id: str) -> dict[str, Any]:
    """Read the evaluations row for one job via the parity read port.

    Args:
        store: Connected PostgresStore.
        job_id: Store-assigned job identity.

    Returns:
        The single evaluations row as a mapping.
    """
    rows = await store.read_all_rows("evaluations")
    matches = [dict(row) for row in rows if str(row["job_id"]) == str(job_id)]
    assert len(matches) == 1, f"expected one evaluations row for {job_id}"
    return matches[0]


async def _pg_execute(url: str, sql: str, *args: Any) -> None:
    """Run a raw statement against the test database.

    Args:
        url: PostgreSQL DSN.
        sql: SQL statement with $N placeholders.
        args: Bound parameters.
    """
    conn = await asyncpg.connect(url)
    try:
        await conn.execute(sql, *args)
    finally:
        await conn.close()


async def test_save_job_upserts_and_preserves_enrichment(store: PostgresStore) -> None:
    """Upserts should update mutable fields without erasing enriched JD data."""
    inserted = await store.save_job(make_store_job())
    incoming = make_store_job(jd_text=None)
    incoming.title = "Updated Backend Intern"

    updated = await store.save_job(incoming)
    jobs = await store.list_jobs()
    loaded = await store.get_job(inserted.job_id)

    assert updated.job_id == inserted.job_id
    assert updated.inserted is False
    assert updated.updated is True
    assert len(jobs) == 1
    assert loaded is not None
    assert loaded.title == "Updated Backend Intern"
    assert loaded.jd_text == "Detailed JD"
    assert loaded.jd_quality is QualityBand.GOOD
    assert loaded.posted_at is not None
    assert loaded.enriched_at is not None
    assert loaded.enrich_source == "mock"


async def test_get_enrichment_returns_snapshot_by_natural_key(
    store: PostgresStore,
) -> None:
    """get_enrichment returns the stored JD snapshot, or None for an unknown key."""
    job = make_store_job("li-1")
    await store.save_job(job)

    snapshot = await store.get_enrichment(platform=job.platform, canonical_id="li-1")

    assert snapshot is not None
    assert snapshot.jd_text == "Detailed JD"
    assert snapshot.quality is QualityBand.GOOD
    assert snapshot.enriched_at is not None
    assert snapshot.enrich_source == "mock"
    assert (
        await store.get_enrichment(platform=job.platform, canonical_id="absent")
    ) is None


async def test_get_closed_canonical_ids_scopes_to_platform_and_closed(
    store: PostgresStore,
) -> None:
    """Returns definitive closures only, scoped to the requested platform."""
    closed_at = datetime.now(UTC)
    await store.save_job(
        make_job("sa-dead", platform="speedyapply", jd_text=None, closed_at=closed_at)
    )
    await store.save_job(
        make_job(
            "sa-gone",
            platform="speedyapply",
            jd_text=None,
            closed_at=closed_at,
            enrich_error="gone:404:greenhouse",
        )
    )
    await store.save_job(
        make_job(
            "sa-stale",
            platform="speedyapply",
            jd_text=None,
            closed_at=closed_at,
            enrich_error="backfill:stale-no-jd",
        )
    )
    await store.save_job(
        make_job("sa-live", platform="speedyapply", jd_text="Detailed JD")
    )
    await store.save_job(
        make_job("li-dead", platform="linkedin", jd_text=None, closed_at=closed_at)
    )

    closed = await store.get_closed_canonical_ids(platform="speedyapply")

    # sa-stale is a heuristic mark-stale-closed backfill, not a proven-gone
    # posting: it stays out of the set so the JD re-fetch (and the save-path
    # self-heal that clears closed_at on a successful fetch) can recover it.
    # Live row + other-platform row are excluded as before.
    assert closed == {"sa-dead", "sa-gone"}
    assert await store.get_closed_canonical_ids(platform="unknown") == set()


async def test_load_pending_stage_a_corpus_filters(store: PostgresStore) -> None:
    """corpus selects unrated (no eval or error), failed (errors), or all."""
    await store.save_job(make_store_job("unrated"))
    failed = await store.save_job(make_store_job("failed"))
    completed = await store.save_job(make_store_job("completed"))
    await store.save_stage_a_error(failed.job_id, "boom")
    await store.save_stage_a(completed.job_id, make_stage_a())

    default_ids = {j.canonical_id for j in await store.load_pending_stage_a()}
    failed_ids = {
        j.canonical_id for j in await store.load_pending_stage_a(corpus="failed")
    }
    all_ids = {j.canonical_id for j in await store.load_pending_stage_a(corpus="all")}

    assert default_ids == {"unrated", "failed"}
    assert failed_ids == {"failed"}
    assert all_ids == {"unrated", "failed", "completed"}


async def test_load_pending_stage_a_quality_and_freshness_filters(
    store: PostgresStore,
) -> None:
    """quality_bands filters by jd_quality and max_days filters by recency."""
    now = datetime.now(UTC)
    await store.save_job(
        make_job(
            "fresh-good", jd_text="JD", jd_quality=QualityBand.GOOD, discovered_at=now
        )
    )
    await store.save_job(
        make_job(
            "fresh-partial",
            jd_text="JD",
            jd_quality=QualityBand.PARTIAL,
            discovered_at=now,
        )
    )
    await store.save_job(
        make_job(
            "stale-good",
            jd_text="JD",
            jd_quality=QualityBand.GOOD,
            discovered_at=now - timedelta(days=30),
        )
    )

    quality_ids = {
        j.canonical_id
        for j in await store.load_pending_stage_a(quality_bands=frozenset({"good"}))
    }
    fresh_ids = {j.canonical_id for j in await store.load_pending_stage_a(max_days=7)}

    assert quality_ids == {"fresh-good", "stale-good"}
    assert fresh_ids == {"fresh-good", "fresh-partial"}


async def test_stage_a_retry_cap_excludes_jobs_over_limit(store: PostgresStore) -> None:
    """Errored jobs retry up to the cap, then drop out of the pending queue."""
    saved = await store.save_job(make_store_job("capped"))
    await store.save_stage_a_error(saved.job_id, "boom-1")
    await store.save_stage_a_error(saved.job_id, "boom-2")
    under_cap = {j.canonical_id for j in await store.load_pending_stage_a()}

    await store.save_stage_a_error(saved.job_id, "boom-3")
    over_cap = {j.canonical_id for j in await store.load_pending_stage_a()}

    assert "capped" in under_cap
    assert "capped" not in over_cap


async def test_stage_completion_timestamps_are_stamped_and_immutable(
    store: PostgresStore,
) -> None:
    """save_stage_a/b stamp stage_a_at/stage_b_at once and preserve them."""
    saved = await store.save_job(make_store_job())
    await store.save_stage_a(saved.job_id, make_stage_a())
    first = await _eval_row(store, saved.job_id)
    assert first["stage_a_at"] is not None

    await store.save_stage_b(saved.job_id, make_stage_b())
    after_b = await _eval_row(store, saved.job_id)

    assert after_b["stage_b_at"] is not None
    assert after_b["stage_a_at"] == first["stage_a_at"]


async def test_evaluations_track_created_and_updated_timestamps(
    store: PostgresStore,
) -> None:
    """Evaluation rows should include timestamp columns for later auditing."""
    saved = await store.save_job(make_store_job("timestamps"))

    await store.save_stage_a(saved.job_id, make_stage_a())
    row = await _eval_row(store, saved.job_id)

    assert row["created_at"] is not None
    assert row["updated_at"] is not None


async def test_stage_b_persists_raw_blocks_and_separate_metadata(
    store: PostgresStore,
) -> None:
    """Stage B persistence should preserve raw blocks and not overwrite Stage A."""
    saved = await store.save_job(make_store_job())
    await store.save_stage_a(saved.job_id, make_stage_a())
    await store.save_stage_b(saved.job_id, make_stage_b())

    row = await _eval_row(store, saved.job_id)
    evaluations = await store.list_evaluated_jobs()

    fit = row["stage_b_fit_json"]
    fit_obj = json.loads(fit) if isinstance(fit, str) else fit
    assert fit_obj["score_0_100"] == STAGE_B_SCORE
    assert row["stage_a_model"] == "mock/stage-a"
    assert row["stage_b_model"] == "mock/stage-b"
    assert row["stage_a_prompt_hash"] == "stage-a-prompt"
    assert row["stage_b_prompt_hash"] == "stage-b-prompt"
    assert evaluations[0].stage_a is not None
    assert evaluations[0].stage_b is not None
    assert evaluations[0].stage_a.cost_usd == STAGE_A_COST
    assert evaluations[0].stage_b.cost_usd == STAGE_B_COST
    assert evaluations[0].stage_b.raw_blocks == json.loads(make_stage_b_raw())


async def test_list_evaluated_jobs_keeps_job_id_when_eval_id_differs(
    store: PostgresStore,
) -> None:
    """Joined evaluation rows should not let evaluations.id override jobs.id."""
    first = await store.save_job(make_store_job("first"))
    second = await store.save_job(make_store_job("second"))
    await store.save_stage_a(second.job_id, make_stage_a())

    evaluations = await store.list_evaluated_jobs()

    assert first.job_id != second.job_id
    assert evaluations[0].job.id == second.job_id


async def test_reapply_notice_detects_interview_substage(store: PostgresStore) -> None:
    """Reapply notice flags an active same-company app in an interview stage."""
    first = await store.save_job(make_store_job("co-first"))
    second = await store.save_job(make_store_job("co-second"))
    await store.transition_status(
        TransitionRequest(job_id=first.job_id, new_status="applied", force=True)
    )
    await store.transition_status(
        TransitionRequest(job_id=first.job_id, new_status="interviewing")
    )

    notice = await store.compute_reapply_notice(job_id=second.job_id)

    assert notice is not None
    assert "interviewing" in notice


async def test_transition_followup_lifecycle(store: PostgresStore) -> None:
    """next_followup_at is set on applied, kept across substages, cleared on close."""
    saved = await store.save_job(make_store_job("followup"))
    await store.transition_status(
        TransitionRequest(job_id=saved.job_id, new_status="applied", force=True)
    )
    applied = await store.get_status(saved.job_id)
    assert applied is not None
    assert applied.next_followup_at is not None

    await store.transition_status(
        TransitionRequest(job_id=saved.job_id, new_status="interviewing")
    )
    advanced = await store.get_status(saved.job_id)
    assert advanced is not None
    assert advanced.next_followup_at == applied.next_followup_at

    await store.transition_status(
        TransitionRequest(job_id=saved.job_id, new_status="rejected", force=True)
    )
    closed = await store.get_status(saved.job_id)
    assert closed is not None
    assert closed.next_followup_at is None


async def test_workflow_attention_warns_interview_stage(
    store: PostgresStore, pg_url: str
) -> None:
    """going_ghosted warns on decay-eligible stages (interviewing), not just applied."""
    saved = await store.save_job(make_store_job("ghost-interview"))
    await store.transition_status(
        TransitionRequest(job_id=saved.job_id, new_status="applied", force=True)
    )
    await store.transition_status(
        TransitionRequest(job_id=saved.job_id, new_status="interviewing")
    )
    # Backdate into the going-ghosted warn window (warn_days = 30 - 5 = 25).
    await _pg_execute(
        pg_url,
        "UPDATE job_status SET last_status_change_at = $1 WHERE job_id = $2",
        datetime.now(UTC) - timedelta(days=29),
        int(saved.job_id),
    )

    attention = await store.workflow_attention(auto_ghost_days=30, lookahead_days=5)

    ghost_ids = {item.job_id for item in attention.going_ghosted}
    assert saved.job_id in ghost_ids


async def test_ml_gate_check_rejects_invalid_score(
    store: PostgresStore, pg_url: str
) -> None:
    """The schema CHECK rejects an out-of-range ML gate score on jobs."""
    saved = await store.save_job(make_store_job("ml-bad"))

    with pytest.raises(asyncpg.PostgresError):
        await _pg_execute(
            pg_url,
            "UPDATE jobs SET ml_gate_score = 1.4 WHERE id = $1",
            int(saved.job_id),
        )


async def test_save_job_resets_ml_gate_when_jd_text_changes(
    store: PostgresStore,
) -> None:
    """A re-scan with a changed winning JD clears the stale gate verdict.

    The gate's verdict is keyed on the JD content. When a re-scan replaces the
    stored JD with a different one of equal-or-higher quality (so the new JD
    actually WINS the upsert), the persisted ml_gate_* verdict no longer
    describes the live content: a stale 'fail' would be excluded forever and a
    stale 'pass' trusted without re-gating. save_job must NULL the ml_gate_*
    columns so the funnel re-gates on the new JD.
    """
    saved = await store.save_job(make_store_job("jd-change"))
    await store.save_ml_gate_result(saved.job_id, make_ml_gate("fail"))
    before = await _ml_gate_row(store, saved.job_id)
    assert before["ml_gate_result"] == "fail"

    # Same quality band (GOOD) so the new JD wins the upsert, but different text.
    changed = make_store_job("jd-change", jd_text="A completely rewritten JD body")
    await store.save_job(changed)

    after = await _ml_gate_row(store, saved.job_id)
    assert all(after[col] is None for col in ML_GATE_COLUMNS)


async def test_save_job_preserves_ml_gate_when_content_unchanged(
    store: PostgresStore,
) -> None:
    """An identical re-scan keeps the gate verdict (no needless re-gate).

    Decision 8: don't re-gate an unchanged re-scan and risk a model-drift flip.
    Re-saving the SAME (platform, canonical_id) with identical title + jd_text
    leaves the winning JD equal to the stored one, so the ml_gate_* verdict is
    preserved intact.
    """
    saved = await store.save_job(make_store_job("jd-same"))
    await store.save_ml_gate_result(saved.job_id, make_ml_gate("pass"))

    # Re-save an identical posting (same title + same jd_text).
    await store.save_job(make_store_job("jd-same"))

    after = await _ml_gate_row(store, saved.job_id)
    assert after["ml_gate_result"] == "pass"
    assert after["ml_gate_score"] is not None
    assert after["ml_gate_version"] == "gate-v1"
    assert after["ml_gate_at"] is not None


async def test_save_job_resets_ml_gate_on_title_only_change(
    store: PostgresStore,
) -> None:
    """A title-only re-scan also clears the stale gate verdict.

    Title is a gate input feature alongside the JD, so a changed title with an
    unchanged JD must still reset the verdict for re-gating.
    """
    saved = await store.save_job(make_store_job("title-change"))
    await store.save_ml_gate_result(saved.job_id, make_ml_gate("fail"))

    # Identical jd_text, changed title.
    renamed = make_store_job("title-change")
    renamed.title = "Senior Backend Engineer"
    await store.save_job(renamed)

    after = await _ml_gate_row(store, saved.job_id)
    assert all(after[col] is None for col in ML_GATE_COLUMNS)


async def test_save_job_preserves_ml_gate_when_lower_quality_jd_loses(
    store: PostgresStore,
) -> None:
    """A lower-quality re-scan that loses keeps the stored JD AND the verdict.

    The reset keys on the WINNING JD, not the incoming one. A no-JD/lower-quality
    re-scan (e.g. a transient vendor failure) preserves the stored full JD via
    the quality-gated upsert, so the gate input is effectively unchanged and the
    verdict must be preserved (not reset).
    """
    saved = await store.save_job(make_store_job("jd-loses"))
    await store.save_ml_gate_result(saved.job_id, make_ml_gate("pass"))

    # A re-scan that brought back NO JD (loses the quality gate) keeps stored JD.
    await store.save_job(make_store_job("jd-loses", jd_text=None))

    after = await _ml_gate_row(store, saved.job_id)
    assert after["ml_gate_result"] == "pass"
    assert after["ml_gate_version"] == "gate-v1"


async def test_record_enrichment_resets_ml_gate_verdict(
    store: PostgresStore,
) -> None:
    """record_enrichment replacing the JD clears the stale gate verdict.

    record_enrichment is a deliberate enrichment write that swaps in new JD
    content (it already unconditionally NULLs enrich_error + closed_at). The
    persisted ml_gate_* verdict was computed against the OLD JD, so leaving it
    in place means a stale 'fail' is excluded from the funnel forever and a
    stale 'pass' is trusted without re-gating the new body. The enrichment IS a
    content change, so all five ml_gate_* columns must be NULLed unconditionally.
    """
    saved = await store.save_job(make_store_job("enrich-gate"))
    await store.save_ml_gate_result(saved.job_id, make_ml_gate("fail"))
    before = await _ml_gate_row(store, saved.job_id)
    assert before["ml_gate_result"] == "fail"

    await store.record_enrichment(
        job_id=saved.job_id,
        jd_text="A freshly enriched job description body.",
        jd_quality=QualityBand.FULL.value,
        enriched_at=datetime.now(UTC),
        enrich_source="manual-paste",
    )

    after = await _ml_gate_row(store, saved.job_id)
    assert all(after[col] is None for col in ML_GATE_COLUMNS)


async def test_enrich_paste_resets_ml_gate_verdict(
    store: PostgresStore,
) -> None:
    """enrich_paste clears the stale gate verdict via record_enrichment.

    enrich_paste resolves the job then delegates to record_enrichment, so the
    same JD-replacement reset must apply: a previously-persisted ml_gate_*
    verdict no longer describes the pasted content and must be NULLed.
    """
    job = make_store_job("paste-gate")
    saved = await store.save_job(job)
    await store.save_ml_gate_result(saved.job_id, make_ml_gate("pass"))
    before = await _ml_gate_row(store, saved.job_id)
    assert before["ml_gate_result"] == "pass"

    await store.enrich_paste(
        platform=job.platform,
        canonical_id=job.canonical_id,
        jd_text="Pasted job description body recovered manually.",
    )

    after = await _ml_gate_row(store, saved.job_id)
    assert all(after[col] is None for col in ML_GATE_COLUMNS)


async def test_evaluations_foreign_key_requires_existing_job(
    store: PostgresStore,
) -> None:
    """Evaluation rows should not persist without a matching job row."""
    with pytest.raises(asyncpg.PostgresError):
        await store.save_stage_a_error("999999", "missing job")


async def test_auto_seed_trigger_fires_on_insert(store: PostgresStore) -> None:
    """Inserting a job should auto-seed job_status and job_status_history."""
    saved = await store.save_job(make_store_job("trigger-test"))

    status_rows = [
        dict(r)
        for r in await store.read_all_rows("job_status")
        if str(r["job_id"]) == str(saved.job_id)
    ]
    history_rows = [
        dict(r)
        for r in await store.read_all_rows("job_status_history")
        if str(r["job_id"]) == str(saved.job_id)
    ]

    assert len(status_rows) == 1
    assert status_rows[0]["status"] == "new"
    assert len(history_rows) == 1
    assert history_rows[0]["to_status"] == "new"
    assert history_rows[0]["from_status"] is None
