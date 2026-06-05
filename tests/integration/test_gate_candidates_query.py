"""@postgres integration tests for the Phase 5 funnel store helpers.

Covers ``load_gate_candidates`` (a NON-claiming read that adds the ML-gate +
not-yet-Stage-A-scored predicates on top of the shared Stage A eligibility
filters, and surfaces a stale ``in_progress`` row past the claim TTL) and
``claim_stage_a_by_ids`` (an atomic claim restricted to an explicit id set).
Tests seed their own rows and assert:

- ``load_gate_candidates`` returns ``GateCandidate``s (job + persisted
  ``ml_gate_result``), honors ``exclude_gate_failed`` (excludes
  ``ml_gate_result='fail'``; includes NULL and ``'pass'``-but-unscored),
  excludes already-Stage-A-scored rows, surfaces a stale ``in_progress`` row
  (and excludes a FRESH one), and writes NO ``evaluations`` row.
- freshness uses ``discovered_at`` and the eligible set is capped at ``limit``.
- a ``closed_at``-stamped (confirmed-gone) row is excluded from BOTH consumers
  via the shared job-liveness predicate (neither loaded nor claim-by-id).
- ``claim_stage_a_by_ids`` marks ONLY the given ids ``in_progress``, leaves
  others untouched, and writes nothing for an empty id list.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from jobfeed.adapters.store.postgres import PostgresStore
from jobfeed.domain.models import QualityBand, StageAResult
from tests.support.factories import make_job

pytestmark = pytest.mark.postgres

LIMIT_CAP = 3


def _make_job(canonical_id: str, *, discovered_at: datetime | None = None) -> Any:
    """Create a GOOD-quality job posting for funnel store tests.

    Each row gets a DISTINCT company derived from ``canonical_id`` so its
    ``(company_norm, title_norm)`` twin key is unique — i.e. every ``_make_job``
    row is its OWN singleton dedup cluster. This isolates these tests from the
    twin-cluster suppression in the gate-candidates query; tests that DO want
    colliding twin keys use ``_make_twin`` instead.

    Args:
        canonical_id: Source-specific natural identity.
        discovered_at: Optional discovery timestamp (drives freshness).

    Returns:
        Job posting with deterministic JD/quality and a unique company.
    """
    return make_job(
        canonical_id,
        company=f"Company {canonical_id}",
        jd_text="Detailed JD",
        jd_quality=QualityBand.GOOD,
        discovered_at=discovered_at or datetime.now(UTC),
    )


def _make_twin(canonical_id: str, *, company: str = "Example") -> Any:
    """Create a GOOD-quality job sharing a fixed (company, title) twin key.

    All twins keep the default ``title`` and the given ``company`` so their
    persisted ``(company_norm, title_norm)`` collide — the same soft dedup key
    ``twin_key`` clusters on. Passing ``company=""`` makes a BLANK-company twin
    (``company_norm = ''``), which dedupe treats as its OWN singleton cluster.

    Args:
        canonical_id: Source-specific natural identity (distinct per twin).
        company: Company name; ``""`` yields a blank ``company_norm``.

    Returns:
        Job posting whose twin key collides with same-``company`` siblings.
    """
    return make_job(
        canonical_id,
        company=company,
        jd_text="Detailed JD",
        jd_quality=QualityBand.GOOD,
        discovered_at=datetime.now(UTC),
    )


def _stage_a() -> StageAResult:
    """Create a completed Stage A result for seeding scored rows.

    Returns:
        Stage A result with deterministic metadata.
    """
    return StageAResult(
        score=80,
        one_line="Good fit",
        timing_eligible="eligible",
        model="mock/stage-a",
        prompt_hash="stage-a-prompt",
        resume_hash="resume-a",
        cost_usd=0.10,
    )


async def _set_gate(store: PostgresStore, job_id: str, result: str) -> None:
    """Stamp ``ml_gate_result`` directly on a job row.

    Args:
        store: Connected PostgresStore.
        job_id: Store-assigned job identity.
        result: ``'pass'`` or ``'fail'``.
    """
    conn = await store._get_pool().acquire()  # type: ignore[attr-defined]
    try:
        await conn.execute(
            "UPDATE jobs SET ml_gate_result = $1 WHERE id = $2",
            result,
            int(job_id),
        )
    finally:
        await store._get_pool().release(conn)  # type: ignore[attr-defined]


async def _set_closed(store: PostgresStore, job_id: str) -> None:
    """Stamp ``closed_at = now()`` on a job row (confirmed-gone req).

    Mirrors a JD fetch that returned 404/410/403; the job-liveness predicate in
    ``_stage_a_pending_filters`` must then exclude the row from every Stage-A
    consumer.

    Args:
        store: Connected PostgresStore.
        job_id: Store-assigned job identity.
    """
    conn = await store._get_pool().acquire()  # type: ignore[attr-defined]
    try:
        await conn.execute(
            "UPDATE jobs SET closed_at = now() WHERE id = $1",
            int(job_id),
        )
    finally:
        await store._get_pool().release(conn)  # type: ignore[attr-defined]


async def _set_in_progress(
    store: PostgresStore, job_id: str, *, age: timedelta
) -> None:
    """Stamp an ``in_progress`` Stage-A claim with an aged ``updated_at``.

    Inserts (or updates) the evaluation row to ``in_progress`` with no score and
    ``updated_at = now() - age``, simulating a scorer that claimed the row then
    crashed. ``age`` past the 1h claim TTL makes it a STALE claim.

    Args:
        store: Connected PostgresStore.
        job_id: Store-assigned job identity.
        age: How far in the past to set ``updated_at``.
    """
    conn = await store._get_pool().acquire()  # type: ignore[attr-defined]
    try:
        await conn.execute(
            """INSERT INTO evaluations (job_id, stage_a_status, updated_at)
               VALUES ($1, 'in_progress', now() - $2::interval)
               ON CONFLICT (job_id) DO UPDATE SET
                   stage_a_status = 'in_progress',
                   stage_a_score = NULL,
                   updated_at = now() - $2::interval""",
            int(job_id),
            age,
        )
    finally:
        await store._get_pool().release(conn)  # type: ignore[attr-defined]


async def _stage_a_status(store: PostgresStore, job_id: str) -> str | None:
    """Read the evaluations.stage_a_status for one job (None if no row).

    Args:
        store: Connected PostgresStore.
        job_id: Store-assigned job identity.

    Returns:
        The stage_a_status string, or None when no evaluation row exists.
    """
    conn = await store._get_pool().acquire()  # type: ignore[attr-defined]
    try:
        return await conn.fetchval(
            "SELECT stage_a_status FROM evaluations WHERE job_id = $1",
            int(job_id),
        )
    finally:
        await store._get_pool().release(conn)  # type: ignore[attr-defined]


async def _evaluations_count(store: PostgresStore) -> int:
    """Count rows in the evaluations table.

    Args:
        store: Connected PostgresStore.

    Returns:
        Total evaluation row count.
    """
    return await store.count_rows("evaluations")


async def test_load_gate_candidates_filters_gate_and_scored(
    store: PostgresStore,
) -> None:
    """exclude_gate_failed drops 'fail', keeps NULL + 'pass', excludes scored."""
    null_gate = await store.save_job(_make_job("null-gate"))
    pass_unscored = await store.save_job(_make_job("pass-unscored"))
    gate_failed = await store.save_job(_make_job("gate-failed"))
    pass_scored = await store.save_job(_make_job("pass-scored"))

    await _set_gate(store, pass_unscored.job_id, "pass")
    await _set_gate(store, gate_failed.job_id, "fail")
    await _set_gate(store, pass_scored.job_id, "pass")
    # Already-Stage-A-scored: a completed evaluation row must be excluded.
    await store.save_stage_a(pass_scored.job_id, _stage_a())

    excluded = await store.load_gate_candidates(
        corpus="all",
        quality_bands=None,
        max_days=None,
        limit=100,
        exclude_gate_failed=True,
    )
    ids = {c.job.canonical_id for c in excluded}

    assert ids == {"null-gate", "pass-unscored"}
    assert all(isinstance(c.job.id, str) for c in excluded)
    # gate_failed dropped; pass_scored dropped (already completed Stage A).
    assert "gate-failed" not in ids
    assert "pass-scored" not in ids
    # Identity is preserved for the seeded rows.
    assert {null_gate.job_id, pass_unscored.job_id} == {c.job.id for c in excluded}
    # Persisted gate state is surfaced per row (NULL vs 'pass').
    surfaced = {c.job.canonical_id: c.ml_gate_result for c in excluded}
    assert surfaced == {"null-gate": None, "pass-unscored": "pass"}


async def test_load_gate_candidates_recovers_stale_in_progress_under_unrated(
    store: PostgresStore,
) -> None:
    """A stale ``in_progress`` row re-enters the funnel under ``corpus='unrated'``.

    The default ``unrated`` corpus previously admitted only NULL/error Stage-A
    rows, stranding a scorer-crash ``in_progress`` row forever. The gate-
    candidates query now shares the claim's stale-takeover predicate, so a row
    stuck ``in_progress`` past the 1h claim TTL is surfaced (and re-claimable),
    while a FRESH ``in_progress`` row (actively owned) stays excluded.
    """
    stale = await store.save_job(_make_job("stale"))
    fresh = await store.save_job(_make_job("fresh"))
    await _set_in_progress(store, stale.job_id, age=timedelta(hours=2))
    await _set_in_progress(store, fresh.job_id, age=timedelta(minutes=5))

    candidates = await store.load_gate_candidates(
        corpus="unrated",
        quality_bands=None,
        max_days=None,
        limit=100,
        exclude_gate_failed=True,
    )

    ids = {c.job.canonical_id for c in candidates}
    assert "stale" in ids  # past the TTL → recovered
    assert "fresh" not in ids  # within the TTL → still owned, excluded
    # The load is non-claiming: the stale row stays 'in_progress' (untouched).
    assert await _stage_a_status(store, stale.job_id) == "in_progress"


async def test_load_gate_candidates_includes_fail_when_not_excluding(
    store: PostgresStore,
) -> None:
    """exclude_gate_failed=False ignores gate state entirely."""
    await store.save_job(_make_job("null-gate"))
    passed = await store.save_job(_make_job("passed"))
    failed = await store.save_job(_make_job("failed"))

    await _set_gate(store, passed.job_id, "pass")
    await _set_gate(store, failed.job_id, "fail")

    candidates = await store.load_gate_candidates(
        corpus="all",
        quality_bands=None,
        max_days=None,
        limit=100,
        exclude_gate_failed=False,
    )
    ids = {c.job.canonical_id for c in candidates}

    assert ids == {"null-gate", "passed", "failed"}


async def test_load_gate_candidates_writes_no_evaluation_row(
    store: PostgresStore,
) -> None:
    """The load is non-claiming: no evaluations/in_progress row is created."""
    saved = await store.save_job(_make_job("untouched"))

    loaded = await store.load_gate_candidates(
        corpus="all",
        quality_bands=None,
        max_days=None,
        limit=100,
        exclude_gate_failed=True,
    )

    assert {c.job.canonical_id for c in loaded} == {"untouched"}
    # No evaluation row was written, so stage_a_status is untouched (None).
    assert await _stage_a_status(store, saved.job_id) is None
    assert await _evaluations_count(store) == 0


async def test_load_gate_candidates_freshness_uses_discovered_at(
    store: PostgresStore,
) -> None:
    """max_days filters on discovered_at; stale rows drop out."""
    now = datetime.now(UTC)
    await store.save_job(_make_job("fresh", discovered_at=now))
    await store.save_job(_make_job("stale", discovered_at=now - timedelta(days=30)))

    fresh = await store.load_gate_candidates(
        corpus="all",
        quality_bands=None,
        max_days=7,
        limit=100,
        exclude_gate_failed=True,
    )

    assert {c.job.canonical_id for c in fresh} == {"fresh"}


async def test_load_gate_candidates_respects_limit(store: PostgresStore) -> None:
    """The eligible set is capped at limit (newest first)."""
    now = datetime.now(UTC)
    for i in range(5):
        await store.save_job(
            _make_job(f"job-{i}", discovered_at=now - timedelta(minutes=i))
        )

    limited = await store.load_gate_candidates(
        corpus="all",
        quality_bands=None,
        max_days=None,
        limit=LIMIT_CAP,
        exclude_gate_failed=True,
    )

    assert len(limited) == LIMIT_CAP
    # Newest-first ordering: job-0 .. job-2 are the three most recent.
    assert {c.job.canonical_id for c in limited} == {"job-0", "job-1", "job-2"}


async def test_closed_at_row_excluded_from_load_and_claim(
    store: PostgresStore,
) -> None:
    """A ``closed_at``-stamped req never enters the funnel (job-liveness).

    The shared ``_stage_a_pending_filters`` predicate (``jobs.closed_at IS
    NULL``) covers BOTH consumers: a confirmed-gone posting is neither returned
    by ``load_gate_candidates`` nor flipped to ``in_progress`` by
    ``claim_stage_a_by_ids`` (even when its id is explicitly supplied), while an
    otherwise-identical live row passes through both.
    """
    closed = await store.save_job(_make_job("closed"))
    live = await store.save_job(_make_job("live"))
    await _set_closed(store, closed.job_id)

    loaded = await store.load_gate_candidates(
        corpus="all",
        quality_bands=None,
        max_days=None,
        limit=100,
        exclude_gate_failed=True,
    )
    loaded_ids = {c.job.canonical_id for c in loaded}
    assert loaded_ids == {"live"}  # closed row excluded from the gate load
    assert "closed" not in loaded_ids

    claimed = await store.claim_stage_a_by_ids(
        [closed.job_id, live.job_id],
        corpus="all",
    )
    assert {j.canonical_id for j in claimed} == {"live"}  # closed id is a no-op
    # The closed row is never claimed: no in_progress flip (no evaluation row).
    assert await _stage_a_status(store, closed.job_id) is None
    assert await _stage_a_status(store, live.job_id) == "in_progress"


async def test_claim_stage_a_by_ids_claims_only_given_ids(
    store: PostgresStore,
) -> None:
    """Claim marks ONLY the given ids in_progress; others stay untouched."""
    a = await store.save_job(_make_job("claim-a"))
    b = await store.save_job(_make_job("claim-b"))
    c = await store.save_job(_make_job("claim-c"))

    claimed = await store.claim_stage_a_by_ids(
        [a.job_id, b.job_id],
        corpus="all",
    )
    claimed_ids = {j.canonical_id for j in claimed}

    assert claimed_ids == {"claim-a", "claim-b"}
    assert await _stage_a_status(store, a.job_id) == "in_progress"
    assert await _stage_a_status(store, b.job_id) == "in_progress"
    # c was not in the id list → no evaluation row written.
    assert await _stage_a_status(store, c.job_id) is None


async def test_claim_stage_a_by_ids_empty_writes_nothing(
    store: PostgresStore,
) -> None:
    """An empty id list returns [] and writes no evaluation rows."""
    await store.save_job(_make_job("present"))

    claimed = await store.claim_stage_a_by_ids([], corpus="all")

    assert claimed == []
    assert await _evaluations_count(store) == 0


async def test_claim_stage_a_by_ids_skips_malformed_ids_without_raising(
    store: PostgresStore,
) -> None:
    """A batch mixing valid + malformed ids claims only the valid ones.

    Malformed ids (``'--1'``, ``'1-2'``, ``'abc'``) are dropped by
    ``_numeric_job_ids`` rather than crashing the claim with a ``ValueError``, so
    the two real store ids still flip to ``in_progress``.
    """
    a = await store.save_job(_make_job("good-a"))
    b = await store.save_job(_make_job("good-b"))

    claimed = await store.claim_stage_a_by_ids(
        [a.job_id, "--1", "1-2", "abc", b.job_id],
        corpus="all",
    )

    assert {j.canonical_id for j in claimed} == {"good-a", "good-b"}
    assert await _stage_a_status(store, a.job_id) == "in_progress"
    assert await _stage_a_status(store, b.job_id) == "in_progress"


async def test_claim_stage_a_by_ids_all_malformed_writes_nothing(
    store: PostgresStore,
) -> None:
    """An all-malformed id list returns [] and writes no evaluation rows.

    Mirrors the empty-``job_ids`` short-circuit: ``_numeric_job_ids`` yields
    ``[]``, so the claim returns early with no query and no writes.
    """
    await store.save_job(_make_job("present"))

    claimed = await store.claim_stage_a_by_ids(["--1", "abc"], corpus="all")

    assert claimed == []
    assert await _evaluations_count(store) == 0


async def test_claim_stage_a_by_ids_honors_corpus_eligibility(
    store: PostgresStore,
) -> None:
    """The id-claim respects the same corpus predicate as claim_pending_stage_a.

    Under the default ``corpus='unrated'`` a completed Stage A row is NOT
    claimable even when its id is supplied, so the claim leaves it untouched and
    only flips the still-pending row to ``in_progress``. (``corpus='all'`` would
    deliberately re-claim completed rows, mirroring ``claim_pending_stage_a``.)
    """
    done = await store.save_job(_make_job("done"))
    fresh = await store.save_job(_make_job("fresh"))
    await store.save_stage_a(done.job_id, _stage_a())

    claimed = await store.claim_stage_a_by_ids([done.job_id, fresh.job_id])

    assert {j.canonical_id for j in claimed} == {"fresh"}
    # The completed row keeps its 'completed' status (not flipped to in_progress).
    assert await _stage_a_status(store, done.job_id) == "completed"
    assert await _stage_a_status(store, fresh.job_id) == "in_progress"


async def test_load_gate_candidates_suppresses_twin_of_completed_cluster(
    store: PostgresStore,
) -> None:
    """A pending twin is excluded once any cluster member is Stage-A completed.

    Dedupe scores each ``(company_norm, title_norm)`` cluster ONCE. When run 1
    completes the cluster's representative (T1), the completed-exclusion drops T1
    from run 2's load — but the still-pending twin T2 would otherwise re-enter,
    re-elect itself as a new representative, and be re-scored (duplicate LLM
    cost). The gate-candidates query now also excludes any candidate whose
    non-blank twin key already has a Stage-A ``completed`` member, so T2 stays
    out of the funnel.
    """
    t1 = await store.save_job(_make_twin("twin-1"))
    await store.save_job(_make_twin("twin-2"))
    # Run 1 scored the cluster via its representative T1.
    await store.save_stage_a(t1.job_id, _stage_a())

    candidates = await store.load_gate_candidates(
        corpus="all",
        quality_bands=None,
        max_days=None,
        limit=100,
        exclude_gate_failed=True,
    )
    ids = {c.job.canonical_id for c in candidates}

    # T1 dropped (completed); T2 suppressed (cluster already scored).
    assert "twin-1" not in ids
    assert "twin-2" not in ids
    assert ids == set()


async def test_load_gate_candidates_blank_norm_twins_do_not_suppress(
    store: PostgresStore,
) -> None:
    """Blank-company twins never suppress each other (singleton-cluster rule).

    A posting whose ``company_norm`` (or ``title_norm``) is blank forms its OWN
    singleton cluster in dedupe and is never folded with other blank-norm rows.
    The twin-suppression predicate mirrors that: it only fires when BOTH norms
    are non-blank. Two blank-company rows sharing the same blank key — one
    Stage-A completed — must NOT suppress the other (negative control proving
    the blank-norm guard, not a blanket twin-key match).
    """
    blank_done = await store.save_job(_make_twin("blank-done", company=""))
    await store.save_job(_make_twin("blank-pending", company=""))
    await store.save_stage_a(blank_done.job_id, _stage_a())

    candidates = await store.load_gate_candidates(
        corpus="all",
        quality_bands=None,
        max_days=None,
        limit=100,
        exclude_gate_failed=True,
    )
    ids = {c.job.canonical_id for c in candidates}

    # blank_done dropped (completed) but the blank-norm guard keeps blank_pending.
    assert "blank-done" not in ids
    assert "blank-pending" in ids
