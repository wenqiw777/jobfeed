"""Phase 5 evaluate-funnel integration chain (MockLLM + MockGate, real store).

Drives the real ``EvaluateService`` end-to-end through the Phase 5 funnel
(load -> hard-filter -> dedupe -> ML gate -> claim survivors -> Stage A -> Stage
B) against PostgreSQL, using only deterministic doubles: ``MockGate`` (real
``hard_fail_reason`` + a configurable ``fail_if`` model verdict — no embedder)
and ``MockLLM``. It asserts the funnel's load-bearing behaviors:

* representative-only scoring — twin non-reps are never gated or scored;
* counters — ``jobs_filtered`` (hard-filter drops), ``jobs_ml_gated``
  (gate non-pass), ``jobs_scored`` (Stage A + Stage B call-count);
* gate persistence — ``save_ml_gate_result`` runs for every gated rep;
* handoff isolation — hard-failed / gate-failed / never-gated / non-rep rows are
  never claimed, never Stage-A-scored, and never left ``in_progress``;
* crash recovery (Decision 8) — a ``'pass'``-but-unscored row from an
  interrupted run is scored on the next run WITHOUT being re-gated (even if the
  gate would now fail it), and a row stranded ``'in_progress'`` past the claim
  TTL by a scorer crash is recovered under the DEFAULT ``corpus='unrated'`` run
  (a FRESH ``'in_progress'`` row is not);
* dry-run limit — a dry-run previews exactly the Stage A ``--limit`` survivors;
* idempotency — a second run re-gates nothing already Stage-A-scored.

Run via testcontainers (auto-starts PG). Do NOT point ``PGTEST_DSN`` at a real
database: the ``store`` fixture drops and recreates the public schema.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest

from jobfeed.adapters.llm.mock import MockLLM
from jobfeed.adapters.ml.mock import MockGate
from jobfeed.adapters.store.postgres import PostgresStore
from jobfeed.config import Settings
from jobfeed.domain.filtering import HardFilters
from jobfeed.domain.ml_features import MLGateFeatures
from jobfeed.domain.models import JobPosting, Message, MLGateResult, QualityBand
from jobfeed.domain.models_ops import CostEntry
from jobfeed.ports.prompts import PromptBundle
from jobfeed.services.evaluate import EvaluateService
from jobfeed.services.evaluate_types import (
    EvaluateDependencies,
    EvaluateLLMConfig,
    EvaluateRuntimeConfig,
)

pytestmark = pytest.mark.postgres

RESUME_TEXT = "Phase 5 resume placeholder."
BLOCKED_COMPANY = "BlockedCorp"
GATE_FAIL_COMPANY = "GateFailCo"
MOCK_STAGE_A_SCORE = 75  # MockLLM Stage A score; >= default threshold 60.

# JD bodies are padded past SHORT_JD_THRESHOLD (200 chars) so Stage A scores them
# rather than rejecting them as too-short.
_PAD = (
    " The team values strong fundamentals, clear written communication, and a "
    "pragmatic, test-driven approach to shipping reliable production software "
    "every single week without drama."
)
CLEAN_SWE_JD = (
    "Build and operate backend services in Python on Kubernetes. Own APIs, "
    "databases, and reliability for our customer-facing platform." + _PAD
)
GAMEDEV_SWE_JD = (
    "Software Engineer building our game engine in C++ with Unreal Engine. "
    "Also maintain backend services for multiplayer matchmaking in Python." + _PAD
)
MECH_JD = (
    "Design mechanical assemblies and tolerances for high-volume manufacturing "
    "lines. Own CAD models and thermal analysis end to end. No software work." + _PAD
)


@dataclass(frozen=True, kw_only=True)
class SeedJob:
    """One seed posting plus its expected funnel disposition."""

    platform: str
    canonical_id: str
    company: str
    title: str
    jd_text: str


# Twin cluster: same normalized (company, title); greenhouse outranks linkedin
# on source priority, so the greenhouse row is the representative.
TWIN_REP = SeedJob(
    platform="greenhouse",
    canonical_id="twin-gh",
    company="TwinCo",
    title="Backend Engineer",
    jd_text=CLEAN_SWE_JD,
)
TWIN_NONREP = SeedJob(
    platform="linkedin",
    canonical_id="twin-li",
    company="TwinCo",
    title="Backend Engineer",
    jd_text=CLEAN_SWE_JD,
)
HARD_FAIL_JOB = SeedJob(
    platform="greenhouse",
    canonical_id="hardfail",
    company="MechCo",
    title="Mechanical Design Engineer",
    jd_text=MECH_JD,
)
GATE_FAIL_JOB = SeedJob(
    platform="greenhouse",
    canonical_id="gatefail",
    company=GATE_FAIL_COMPANY,
    title="Software Engineer",
    jd_text=GAMEDEV_SWE_JD,
)
HARD_FILTER_JOB = SeedJob(
    platform="greenhouse",
    canonical_id="blocked",
    company=BLOCKED_COMPANY,
    title="Backend Engineer",
    jd_text=CLEAN_SWE_JD,
)
PASS_JOB = SeedJob(
    platform="greenhouse",
    canonical_id="pass",
    company="PassCo",
    title="Platform Engineer",
    jd_text=CLEAN_SWE_JD,
)

CORPUS = [
    TWIN_REP,
    TWIN_NONREP,
    HARD_FAIL_JOB,
    GATE_FAIL_JOB,
    HARD_FILTER_JOB,
    PASS_JOB,
]

EXPECTED_FILTERED = 1  # HARD_FILTER_JOB (company blocklist).
EXPECTED_ML_GATED = 2  # HARD_FAIL_JOB (real rule) + GATE_FAIL_JOB (fail_if).
EXPECTED_SCORED_REPS = 2  # TWIN_REP + PASS_JOB.
EXPECTED_JOBS_SCORED = EXPECTED_SCORED_REPS * 2  # Stage A + Stage B call-count.


def _gamedev_fail(features: MLGateFeatures) -> bool:
    """Model-style fail predicate: fail any gamedev-tagged (non-hard-fail) job."""
    return "gamedev" in features.domain_tags


def _hard_filters() -> HardFilters:
    """Hard filters that block exactly the BlockedCorp posting."""
    return HardFilters(company_blocklist=[BLOCKED_COMPANY])


class RecordingLogger:
    """In-memory logger; the service only needs the structlog-shaped methods."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, object]]] = []

    def info(self, event: str, **kwargs: object) -> object:
        """Record an info event."""
        self.events.append((event, kwargs))
        return None

    def error(self, event: str, **kwargs: object) -> object:
        """Record an error event."""
        self.events.append((event, kwargs))
        return None

    def warning(self, event: str, **kwargs: object) -> object:
        """Record a warning event."""
        self.events.append((event, kwargs))
        return None

    def debug(self, event: str, **kwargs: object) -> object:
        """Record a debug event."""
        self.events.append((event, kwargs))
        return None


class StubStoreOps:
    """Minimal StoreOpsMixin stub: swallows cost/usage writes."""

    async def record_cost(self, *, day: str, spent_usd: float, calls: int = 1) -> None:
        """Ignore cost writes."""

    async def get_cost(self, _day: str) -> None:
        """Report no prior spend (budget always open)."""

    async def record_llm_usage(self, _usage: object) -> None:
        """Ignore usage writes."""

    async def record_llm_usage_with_cost(
        self, *, day: str, spent_usd: float, usage: object
    ) -> None:
        """Ignore combined cost+usage writes."""


class ExhaustedBudgetStoreOps(StubStoreOps):
    """StoreOps stub whose budget reads report the daily call limit reached."""

    async def get_cost(self, day: str) -> CostEntry:
        """Report enough prior calls that ``check_budget`` returns False."""
        return CostEntry(
            day=day,
            spent_usd=0.0,
            calls=10_000,  # >> max_daily_score_calls default (150).
            last_updated=datetime.now(UTC),
        )


class CountingGate:
    """MockGate wrapper that counts ``predict_batch`` invocations."""

    def __init__(self) -> None:
        self.calls = 0
        self._inner = MockGate(default_result="pass")

    async def predict_batch(self, jobs: list[object]) -> list[object]:
        """Delegate to the inner gate, counting each call."""
        self.calls += 1
        return await self._inner.predict_batch(jobs)  # type: ignore[arg-type]


class StubPromptRenderer:
    """Deterministic prompt bundles routed to MockLLM by stage model name."""

    def render_stage_a(self, *, resume_text: str, job: JobPosting) -> PromptBundle:
        """Return a skeleton Stage A bundle echoing the JD."""
        del resume_text
        return PromptBundle(
            messages=[
                Message(role="system", content="score"),
                Message(role="user", content=job.jd_text or ""),
            ],
            prompt_hash="pa",
            resume_hash="ra",
        )

    def render_stage_b(
        self,
        *,
        resume_text: str,
        job: JobPosting,
        stage_a_score: int | None = None,
    ) -> PromptBundle:
        """Return a skeleton Stage B bundle echoing the JD."""
        del resume_text, stage_a_score
        return PromptBundle(
            messages=[
                Message(role="system", content="review"),
                Message(role="user", content=job.jd_text or ""),
            ],
            prompt_hash="pb",
            resume_hash="rb",
        )


def _config(*, ml_gate_max_candidates: int | None = None) -> EvaluateRuntimeConfig:
    """Runtime config with the ML gate enabled at the default threshold.

    ``ml_gate_max_candidates`` overrides the gate budget / load page size so a
    test can force the funnel to paginate past a hard-filtered newest prefix.
    """
    settings = Settings()
    extra = (
        {"ml_gate_max_candidates": ml_gate_max_candidates}
        if ml_gate_max_candidates is not None
        else {}
    )
    return EvaluateRuntimeConfig(
        llm=EvaluateLLMConfig(
            stage_a="mock/stage-a",
            stage_b="mock/stage-b",
            max_concurrent=settings.llm.max_concurrent,
            max_daily_score_calls=settings.llm.max_daily_score_calls,
            max_daily_cost_usd=settings.llm.max_daily_cost_usd,
        ),
        stage_a_threshold=settings.scoring.stage_a_threshold,
        resume_text=RESUME_TEXT,
        ml_gate_enabled=True,
        **extra,
    )


def _service(  # noqa: PLR0913 - keyword-only test knobs; readability over a wrapper
    store: PostgresStore,
    *,
    fail_if: Callable[[MLGateFeatures], bool] | None = _gamedev_fail,
    ml_gate_max_candidates: int | None = None,
    logger: RecordingLogger | None = None,
    store_ops: object | None = None,
    ml_gate: object | None = None,
) -> EvaluateService:
    """Build an EvaluateService wired with MockGate + MockLLM + hard filters.

    ``store_ops`` / ``ml_gate`` override the defaults so a test can exhaust the
    budget (ExhaustedBudgetStoreOps) and/or count gate calls (CountingGate).
    """
    deps = EvaluateDependencies(
        store=store,
        store_ops=store_ops or StubStoreOps(),  # type: ignore[arg-type]
        store_status=store,  # type: ignore[arg-type]
        prompt_renderer=StubPromptRenderer(),  # type: ignore[arg-type]
        llm_stage_a=MockLLM(),
        llm_stage_b=MockLLM(),
        ml_gate=ml_gate or MockGate(default_result="pass", fail_if=fail_if),  # type: ignore[arg-type]
        hard_filters=_hard_filters(),
    )
    return EvaluateService(
        deps=deps,
        config=_config(ml_gate_max_candidates=ml_gate_max_candidates),
        logger=logger or RecordingLogger(),  # type: ignore[arg-type]
    )


async def _seed(store: PostgresStore, jobs: list[SeedJob]) -> dict[str, str]:
    """Insert seed postings (all FULL quality, fresh) and map canonical_id->id."""
    now = datetime.now(UTC)
    ids: dict[str, str] = {}
    for seed in jobs:
        result = await store.save_job(
            JobPosting(
                platform=seed.platform,
                canonical_id=seed.canonical_id,
                url=f"https://example.com/{seed.platform}/{seed.canonical_id}",
                title=seed.title,
                company=seed.company,
                location="Remote",
                discovered_at=now,
                jd_text=seed.jd_text,
                jd_quality=QualityBand.FULL,
                posted_at=now,
            )
        )
        ids[seed.canonical_id] = result.job_id
    return ids


async def _gate_state(store: PostgresStore, job_id: str) -> dict[str, object]:
    """Read the persisted ml_gate columns + Stage A claim/score state for a job."""
    pool = store._get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT j.ml_gate_result, j.ml_gate_fail_reason, j.ml_gate_at,
                      j.ml_gate_score, e.stage_a_status, e.stage_a_score,
                      e.stage_b_status
               FROM jobs j
               LEFT JOIN evaluations e ON e.job_id = j.id
               WHERE j.id = $1""",
            int(job_id),
        )
    assert row is not None
    return dict(row)


async def _strand_in_progress(
    store: PostgresStore, job_id: str, *, age: timedelta
) -> None:
    """Mark a job's Stage-A claim ``in_progress`` with an aged ``updated_at``.

    Simulates a scorer that claimed the row and crashed: status ``in_progress``,
    no score, ``updated_at = now() - age``. ``age`` past the 1h claim TTL makes
    it STALE (recoverable); within the TTL it is FRESH (still owned).
    """
    pool = store._get_pool()
    async with pool.acquire() as conn:
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


async def test_phase5_funnel_scopes_scoring_to_passing_representatives(
    store: PostgresStore,
) -> None:
    """The funnel scores only passing reps; counters + isolation hold."""
    ids = await _seed(store, CORPUS)

    run = await _service(store).run(stage="both")

    # Counters.
    assert run.jobs_filtered == EXPECTED_FILTERED
    assert run.jobs_ml_gated == EXPECTED_ML_GATED
    assert run.stage_a_scored == EXPECTED_SCORED_REPS
    assert run.stage_b_scored == EXPECTED_SCORED_REPS
    assert run.jobs_scored == EXPECTED_JOBS_SCORED
    assert run.errors == 0

    rep = await _gate_state(store, ids["twin-gh"])
    nonrep = await _gate_state(store, ids["twin-li"])
    hard_fail = await _gate_state(store, ids["hardfail"])
    gate_fail = await _gate_state(store, ids["gatefail"])
    blocked = await _gate_state(store, ids["blocked"])
    passing = await _gate_state(store, ids["pass"])

    # Representative-only scoring: rep gated 'pass' + Stage A/B scored.
    assert rep["ml_gate_result"] == "pass"
    assert rep["stage_a_status"] == "completed"
    assert rep["stage_a_score"] == MOCK_STAGE_A_SCORE
    assert rep["stage_b_status"] == "completed"

    # Twin non-rep: never gated (no decision), never claimed, never scored.
    assert nonrep["ml_gate_result"] is None
    assert nonrep["ml_gate_at"] is None
    assert nonrep["stage_a_status"] is None
    assert nonrep["stage_b_status"] is None

    # Hard-fail rep: gate persisted 'fail' with the real rule reason; not scored.
    assert hard_fail["ml_gate_result"] == "fail"
    assert hard_fail["ml_gate_fail_reason"] == "not software engineering role"
    assert hard_fail["stage_a_status"] is None

    # Gate-fail rep: model-style 'fail' (NULL reason); persisted; not scored.
    assert gate_fail["ml_gate_result"] == "fail"
    assert gate_fail["ml_gate_fail_reason"] is None
    assert gate_fail["stage_a_status"] is None

    # Hard-filter-blocked: dropped before the gate — no decision, not scored.
    assert blocked["ml_gate_result"] is None
    assert blocked["ml_gate_at"] is None
    assert blocked["stage_a_status"] is None

    # Clean pass: gated 'pass' + fully scored.
    assert passing["ml_gate_result"] == "pass"
    assert passing["stage_a_status"] == "completed"
    assert passing["stage_b_status"] == "completed"


async def test_phase5_exhausted_budget_skips_funnel_and_gate(
    store: PostgresStore,
) -> None:
    """An exhausted daily budget runs NO funnel: the ML gate is never invoked.

    Regression for the budget-before-funnel fix: with a real gate enabled, an
    already-spent budget can run no Stage A LLM call, so loading candidates +
    embedding + XGBoost + persisting ``ml_gate_*`` would all be wasted. The
    service must short-circuit BEFORE ``run_funnel``: the gate spy records zero
    calls, no row carries an ``ml_gate_result``, and no funnel counter moves.
    """
    ids = await _seed(store, CORPUS)
    gate = CountingGate()

    run = await _service(store, store_ops=ExhaustedBudgetStoreOps(), ml_gate=gate).run(
        stage="a"
    )

    # The gate (and therefore the whole funnel) never ran.
    assert gate.calls == 0
    assert run.jobs_ml_gated == 0
    assert run.jobs_filtered == 0
    assert run.stage_a_scored == 0
    # Nothing was persisted to the ml_gate_* columns for any seeded row.
    pool = store._get_pool()
    decided = await pool.fetch(
        "SELECT 1 FROM jobs WHERE id = ANY($1::bigint[]) "
        "AND ml_gate_result IS NOT NULL",
        [int(jid) for jid in ids.values()],
    )
    assert decided == []


async def test_phase5_dropped_rows_are_not_left_in_progress(
    store: PostgresStore,
) -> None:
    """No hard-failed / gate-failed / filtered / non-rep row ends in_progress."""
    await _seed(store, CORPUS)

    await _service(store).run(stage="both")

    pool = store._get_pool()
    stuck = await pool.fetch(
        "SELECT job_id FROM evaluations WHERE stage_a_status = 'in_progress'"
    )
    assert stuck == []


async def test_phase5_gate_decisions_persisted_for_every_gated_rep(
    store: PostgresStore,
) -> None:
    """Exactly the four gated reps carry an ml_gate decision; drops do not."""
    await _seed(store, CORPUS)

    await _service(store).run(stage="both")

    pool = store._get_pool()
    decided = await pool.fetch(
        "SELECT canonical_id FROM jobs WHERE ml_gate_result IS NOT NULL "
        "ORDER BY canonical_id"
    )
    # twin-gh (pass), gatefail (fail), hardfail (fail), pass (pass). The twin
    # non-rep and the hard-filtered row are never gated.
    assert [r["canonical_id"] for r in decided] == [
        "gatefail",
        "hardfail",
        "pass",
        "twin-gh",
    ]


async def test_phase5_second_run_regates_nothing_already_scored(
    store: PostgresStore,
) -> None:
    """A second run re-gates no scored/gate-failed rep and scores nothing new.

    Uses a twin-free corpus so the claim is exact: a scored rep is excluded by
    the not-yet-Stage-A-scored predicate, and a gate-failed rep by the
    ``exclude_gate_failed`` guard, leaving no candidates for the second pass.
    (A twin cluster behaves differently and intentionally: once the rep is
    scored and drops out, a previously-suppressed twin is promoted and scored —
    covered by the scoping test, not asserted as idempotent here.)
    """
    singletons = [PASS_JOB, GATE_FAIL_JOB, HARD_FAIL_JOB]
    ids = await _seed(store, singletons)
    first_run = await _service(store).run(stage="both")
    assert first_run.stage_a_scored == 1  # only PASS_JOB.
    assert first_run.jobs_ml_gated == EXPECTED_ML_GATED
    first_pass_gate_at = (await _gate_state(store, ids["pass"]))["ml_gate_at"]

    second = await _service(store).run(stage="both")

    # Nothing left to do: scored rep excluded, gate-failed reps excluded.
    assert second.jobs_ml_gated == 0
    assert second.stage_a_scored == 0
    assert second.stage_b_scored == 0
    assert second.jobs_scored == 0
    # The already-scored rep's gate decision (and timestamp) is untouched.
    again_pass = await _gate_state(store, ids["pass"])
    assert again_pass["ml_gate_at"] == first_pass_gate_at
    assert again_pass["stage_a_status"] == "completed"


async def test_phase5_crash_recovery_scores_pass_but_unscored_row(
    store: PostgresStore,
) -> None:
    """A 'pass'-gated-but-unscored row (interrupted run) is scored next run."""
    # Only the recovery posting — a clean SWE job pre-gated 'pass' with no Stage
    # A row, exactly the gate-then-crash state from Decision 8.
    ids = await _seed(store, [PASS_JOB])
    job_id = ids["pass"]
    await store.save_ml_gate_result(
        job_id,
        MLGateResult(score=1.0, result="pass", version="mock"),
    )
    before = await _gate_state(store, job_id)
    assert before["ml_gate_result"] == "pass"
    assert before["stage_a_status"] is None

    run = await _service(store).run(stage="both")

    after = await _gate_state(store, job_id)
    assert run.stage_a_scored == 1
    assert run.stage_b_scored == 1
    assert after["stage_a_status"] == "completed"
    assert after["stage_a_score"] == MOCK_STAGE_A_SCORE
    assert after["stage_b_status"] == "completed"


async def test_phase5_pass_but_unscored_row_not_regated_even_if_gate_would_fail(
    store: PostgresStore,
) -> None:
    """A persisted-'pass' rep is scored WITHOUT re-gating, even if the gate fails.

    Models a model/threshold swap between runs: the row was persisted 'pass',
    then the committed gate changed so it would now FAIL. The funnel must NOT
    re-gate it (re-gating would flip it to 'fail' and silently drop it from
    survivors). The persisted 'pass' (and its ``ml_gate_at`` timestamp) is
    unchanged, save_ml_gate_result is never re-called for it, and it IS scored.
    """
    ids = await _seed(store, [PASS_JOB])
    job_id = ids["pass"]
    await store.save_ml_gate_result(
        job_id, MLGateResult(score=1.0, result="pass", version="mock")
    )
    before = await _gate_state(store, job_id)
    assert before["ml_gate_result"] == "pass"

    # A gate that FAILS every (non-hard-fail) job it is asked to score. If the
    # funnel re-gated the persisted-'pass' rep, it would flip to 'fail'.
    service = _service(store, fail_if=lambda _features: True)
    run = await service.run(stage="both")

    after = await _gate_state(store, job_id)
    # Not re-gated / not re-persisted: verdict + timestamp + score unchanged.
    assert after["ml_gate_result"] == "pass"
    assert after["ml_gate_at"] == before["ml_gate_at"]
    assert after["ml_gate_score"] == before["ml_gate_score"]
    # The pass-but-unscored row IS handed to scoring; no fresh gate counted.
    assert run.jobs_ml_gated == 0
    assert run.stage_a_scored == 1
    assert after["stage_a_status"] == "completed"
    assert after["stage_a_score"] == MOCK_STAGE_A_SCORE


async def test_phase5_stale_in_progress_recovered_under_default_unrated(
    store: PostgresStore,
) -> None:
    """A scorer-crash 'in_progress' row is recovered + scored by a default run.

    A row stranded ``in_progress`` past the claim TTL re-enters the funnel under
    the DEFAULT ``corpus='unrated'`` evaluate path, is re-claimed (the claim's
    stale-takeover fires), and is scored. A FRESH ``in_progress`` row (within the
    TTL, still owned by another run) is NOT recovered and stays ``in_progress``.
    """
    ids = await _seed(store, [PASS_JOB, GATE_FAIL_JOB])
    stale_id = ids["pass"]
    fresh_id = ids["gatefail"]
    # Pre-gate both 'pass' so the gate predicate is not what drives the result;
    # the test isolates the stale-vs-fresh in_progress recovery behavior.
    await store.save_ml_gate_result(
        stale_id, MLGateResult(score=1.0, result="pass", version="mock")
    )
    await store.save_ml_gate_result(
        fresh_id, MLGateResult(score=1.0, result="pass", version="mock")
    )
    await _strand_in_progress(store, stale_id, age=timedelta(hours=2))
    await _strand_in_progress(store, fresh_id, age=timedelta(minutes=5))

    # Default corpus='unrated'. No fail_if so the gate would pass — but both rows
    # are already 'pass', so neither is re-gated regardless.
    run = await _service(store, fail_if=None).run(stage="a")

    stale = await _gate_state(store, stale_id)
    fresh = await _gate_state(store, fresh_id)
    # Stale row recovered + scored.
    assert run.stage_a_scored == 1
    assert stale["stage_a_status"] == "completed"
    assert stale["stage_a_score"] == MOCK_STAGE_A_SCORE
    # Fresh row left strictly alone (still owned by the simulated live run).
    assert fresh["stage_a_status"] == "in_progress"
    assert fresh["stage_a_score"] is None


async def test_phase5_dry_run_previews_exactly_the_stage_a_limit(
    store: PostgresStore,
) -> None:
    """A dry-run with more survivors than ``--limit`` previews exactly ``limit``.

    Real runs gate all survivors then claim only ``limit``; the dry-run preview
    must match — slicing survivors to ``limit`` in the claim's
    ``discovered_at DESC, id DESC`` order rather than over-reporting every
    survivor up to ``ml_gate_max_candidates``.
    """
    # Three clean, distinct passing reps → three survivors; the limit keeps the
    # rows claimed first (newest discovered_at, then highest id).
    stage_a_limit = 2
    survivor_seeds = [
        SeedJob(
            platform="greenhouse",
            canonical_id=f"surv-{i}",
            company=f"SurvCo{i}",
            title=f"Backend Engineer {i}",
            jd_text=CLEAN_SWE_JD,
        )
        for i in range(3)
    ]
    ids = await _seed(store, survivor_seeds)

    run = await _service(store, fail_if=None).run(
        stage="a", limit=stage_a_limit, dry_run=True
    )

    preview_ids = [item.job_id for item in run.dry_run_preview]
    assert len(preview_ids) == stage_a_limit
    # All seeds share discovered_at (seeded "now"), so claim order falls to
    # id DESC: the highest store ids win.
    expected = sorted((ids[s.canonical_id] for s in survivor_seeds), key=int)[
        -stage_a_limit:
    ]
    assert set(preview_ids) == set(expected)


async def _seed_with_discovered_at(
    store: PostgresStore, seed: SeedJob, discovered_at: datetime
) -> str:
    """Insert one FULL-quality posting at an explicit ``discovered_at``."""
    result = await store.save_job(
        JobPosting(
            platform=seed.platform,
            canonical_id=seed.canonical_id,
            url=f"https://example.com/{seed.platform}/{seed.canonical_id}",
            title=seed.title,
            company=seed.company,
            location="Remote",
            discovered_at=discovered_at,
            jd_text=seed.jd_text,
            jd_quality=QualityBand.FULL,
            posted_at=discovered_at,
        )
    )
    return result.job_id


async def test_phase5_funnel_overfetches_past_hard_filtered_newest_prefix(
    store: PostgresStore,
) -> None:
    """An older eligible row behind a full page of blocked rows is not starved.

    Regression for the hard-filter starvation bug: the candidate LOAD is paged
    (page size = ``ml_gate_max_candidates``) and ``apply_hard_filters`` runs in
    memory. If the newest full page is entirely hard-filter-blocked (here: a
    blocklisted high-volume company floods the newest ``discovered_at``), the old
    funnel LIMIT-loaded only that page, discarded all of it, and never reached
    the eligible older row on page 2 — so the same blocked prefix was reloaded
    and dropped every run while the clean job starved. The funnel must now
    overfetch past hard-filtered drops (keyset pagination) until it has enough
    survivors or the eligible set is exhausted, surfacing the older clean job.
    """
    page_size = 3
    now = datetime.now(UTC)
    # A full newest page of blocklisted (hard-filter-blocked) rows.
    for i in range(page_size):
        await _seed_with_discovered_at(
            store,
            SeedJob(
                platform="greenhouse",
                canonical_id=f"blocked-{i}",
                company=BLOCKED_COMPANY,
                title="Backend Engineer",
                jd_text=CLEAN_SWE_JD,
            ),
            now - timedelta(minutes=i),
        )
    # One eligible (clean) row strictly OLDER than the whole blocked prefix, so
    # it sorts onto page 2 under ``discovered_at DESC, id DESC``.
    clean_id = await _seed_with_discovered_at(
        store,
        SeedJob(
            platform="greenhouse",
            canonical_id="clean-old",
            company="CleanCo",
            title="Platform Engineer",
            jd_text=CLEAN_SWE_JD,
        ),
        now - timedelta(hours=2),
    )

    run = await _service(store, fail_if=None, ml_gate_max_candidates=page_size).run(
        stage="both"
    )

    # The blocked page is dropped by the hard filter; the older clean row is
    # reached on page 2, gated 'pass', and scored — NOT starved.
    assert run.jobs_filtered == page_size
    state = await _gate_state(store, clean_id)
    assert state["ml_gate_result"] == "pass"
    assert state["stage_a_status"] == "completed"
    assert state["stage_a_score"] == MOCK_STAGE_A_SCORE
    assert run.stage_a_scored == 1


async def test_phase5_overfetch_caps_survivors_to_gate_budget(
    store: PostgresStore,
) -> None:
    """Overfetch never gates/scores more than ``ml_gate_max_candidates``.

    Regression for the overfetch overshoot: with page_size=2, page 1 yields one
    survivor (1 < 2, full page so the loop continues) and a full page 2 of two
    clean rows would push the count to 3 — nearly 2*page_size — so dedupe+gate
    would run on more than the configured budget. The funnel must slice the
    returned survivors to ``page_size`` (newest-first), so EXACTLY ``page_size``
    rows are gated and scored and the oldest over-budget survivor is left
    untouched for a later run.
    """
    page_size = 2
    now = datetime.now(UTC)
    # Newest row is hard-filter-blocked, so page 1 (the 2 newest) yields 1
    # survivor; pages stay full so the loop fetches page 2.
    await _seed_with_discovered_at(
        store,
        SeedJob(
            platform="greenhouse",
            canonical_id="blocked-newest",
            company=BLOCKED_COMPANY,
            title="Backend Engineer",
            jd_text=CLEAN_SWE_JD,
        ),
        now,
    )
    # Three clean, distinct-company passing reps, strictly older than the blocked
    # row. Paged 2-at-a-time they straddle the page boundary; without the cap all
    # three survive (3 > page_size).
    clean_ids = []
    for i in range(3):
        clean_ids.append(
            await _seed_with_discovered_at(
                store,
                SeedJob(
                    platform="greenhouse",
                    canonical_id=f"clean-{i}",
                    company=f"CleanCo{i}",
                    title="Platform Engineer",
                    jd_text=CLEAN_SWE_JD,
                ),
                now - timedelta(minutes=i + 1),
            )
        )

    run = await _service(store, fail_if=None, ml_gate_max_candidates=page_size).run(
        stage="both"
    )

    # Exactly page_size rows were gated (have an ml_gate_result) and scored — not
    # the 3 that survived the hard filter.
    pool = store._get_pool()
    gated = await pool.fetch(
        "SELECT id FROM jobs WHERE id = ANY($1::bigint[]) "
        "AND ml_gate_result IS NOT NULL",
        [int(jid) for jid in clean_ids],
    )
    assert len(gated) == page_size
    assert run.stage_a_scored == page_size
    # The oldest clean survivor is over budget: not gated, not scored this run.
    oldest = await _gate_state(store, clean_ids[-1])
    assert oldest["ml_gate_result"] is None
    assert oldest["stage_a_status"] is None


async def test_phase5_corpus_all_rescores_completed_row_and_twin(
    store: PostgresStore,
) -> None:
    """``evaluate --corpus all`` re-scores a completed row + its completed twin.

    The funnel is the sole path to survivor ids, so an explicit full
    re-evaluation must surface a previously ``completed`` cluster again. Run 1
    (default ``unrated``) scores the twin cluster's rep and the lone clean row.
    Run 2 with ``corpus='all'`` must re-claim and re-score BOTH (the twin cluster,
    via one re-elected rep, and the singleton), whereas a second ``unrated`` run
    would skip them entirely (completed-exclusion + twin-suppression hold for the
    incremental flow). Confirms the corpus-scoped predicate end-to-end.
    """
    ids = await _seed(store, [TWIN_REP, TWIN_NONREP, PASS_JOB])
    first = await _service(store, fail_if=None).run(stage="a", corpus="unrated")
    # Run 1 scored the twin rep (twin-gh) + the singleton (pass): 2 reps.
    assert first.stage_a_scored == EXPECTED_SCORED_REPS
    assert (await _gate_state(store, ids["twin-gh"]))["stage_a_status"] == "completed"
    assert (await _gate_state(store, ids["pass"]))["stage_a_status"] == "completed"

    # A second DEFAULT run finds nothing: completed rep + suppressed twin + the
    # completed singleton are all excluded by the incremental predicate.
    skipped = await _service(store, fail_if=None).run(stage="a", corpus="unrated")
    assert skipped.stage_a_scored == 0

    # corpus='all' re-admits the whole completed set: the twin cluster is
    # re-scored via one re-elected rep, and the singleton is re-scored too.
    rescored = await _service(store, fail_if=None).run(stage="a", corpus="all")
    assert rescored.stage_a_scored == EXPECTED_SCORED_REPS  # twin rep + singleton.
    assert (await _gate_state(store, ids["pass"]))["stage_a_status"] == "completed"
    # Exactly one of the twin pair is re-claimed (dedupe keeps it once-per-cluster).
    pool = store._get_pool()
    twin_claimed = await pool.fetch(
        "SELECT e.job_id FROM evaluations e "
        "WHERE e.job_id = ANY($1::bigint[]) AND e.stage_a_status = 'completed'",
        [int(ids["twin-gh"]), int(ids["twin-li"])],
    )
    assert len(twin_claimed) == 1


async def _seed_twin_pair_at(
    store: PostgresStore, prefix: str, base: datetime
) -> list[str]:
    """Seed two same-(company,title) twins (one cluster) at adjacent timestamps.

    Both share ``TwinCo`` / ``Backend Engineer`` so they collapse to ONE dedup
    cluster, and both sit at the newest ``discovered_at`` so they occupy the first
    candidate page. Returns their store ids.
    """
    ids = []
    for i, platform in enumerate(("greenhouse", "linkedin")):
        ids.append(
            await _seed_with_discovered_at(
                store,
                SeedJob(
                    platform=platform,
                    canonical_id=f"{prefix}-{platform}",
                    company="TwinCo",
                    title="Backend Engineer",
                    jd_text=CLEAN_SWE_JD,
                ),
                base - timedelta(seconds=i),
            )
        )
    return ids


async def test_phase5_overfetch_pages_past_twin_collapsed_newest_page(
    store: PostgresStore,
) -> None:
    """A newest page that dedupes to 1 rep must page further to fill the budget.

    Regression for the dedupe-blind overfetch: the load loop returned as soon as
    it had ``page_size`` HARD-FILTER survivors, BEFORE dedupe. A newest page made
    entirely of one twin cluster (``page_size`` rows collapsing to a SINGLE
    representative) therefore ended the loop with far fewer reps than the budget,
    and the run never reached the older DISTINCT jobs behind that page — claiming
    1 job when ``ml_gate_max_candidates`` was larger. The loop must now count
    DEDUPED representatives toward the target and keep paging, surfacing the older
    distinct rows so the run claims up to the budget.
    """
    page_size = 2
    now = datetime.now(UTC)
    # Newest full page: a twin pair (one cluster -> one rep) at the front.
    twin_ids = await _seed_twin_pair_at(store, "twin", now)
    # Older DISTINCT (different-company) clean reps, strictly behind the twin page.
    distinct_ids = []
    for i in range(3):
        distinct_ids.append(
            await _seed_with_discovered_at(
                store,
                SeedJob(
                    platform="greenhouse",
                    canonical_id=f"distinct-{i}",
                    company=f"DistinctCo{i}",
                    title="Platform Engineer",
                    jd_text=CLEAN_SWE_JD,
                ),
                now - timedelta(minutes=i + 1),
            )
        )

    run = await _service(store, fail_if=None, ml_gate_max_candidates=page_size).run(
        stage="both"
    )

    # The budget is filled with DEDUPED reps: the twin cluster contributes one
    # rep, and the loop paged on to claim the newest distinct rep too — exactly
    # page_size reps scored, NOT the single twin rep the old loop would stop at.
    assert run.stage_a_scored == page_size
    pool = store._get_pool()
    # Exactly one of the twin pair is scored (the cluster's elected rep).
    twin_scored = await pool.fetch(
        "SELECT e.job_id FROM evaluations e "
        "WHERE e.job_id = ANY($1::bigint[]) AND e.stage_a_status = 'completed'",
        [int(jid) for jid in twin_ids],
    )
    assert len(twin_scored) == 1
    # An older DISTINCT job was surfaced and scored (the newest distinct one),
    # proving the loop paged past the twin-collapsed first page.
    newest_distinct = await _gate_state(store, distinct_ids[0])
    assert newest_distinct["stage_a_status"] == "completed"
    # And the budget cap still holds: the oldest distinct job is left for later.
    oldest_distinct = await _gate_state(store, distinct_ids[-1])
    assert oldest_distinct["stage_a_status"] is None
