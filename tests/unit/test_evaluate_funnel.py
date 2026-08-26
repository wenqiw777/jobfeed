"""Unit tests for the evaluation funnel + claim-by-id handoff.

Covers the non-claiming funnel (load -> hard-filter -> dedupe -> gate) and the
EvaluateService wiring that hands survivor ids to ``claim_stage_a_by_ids``. Uses
MockGate plus an in-memory fake store (no PostgreSQL).
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from jobfeed.adapters.ml.mock import MockGate
from jobfeed.adapters.store.postgres import _numeric_job_ids
from jobfeed.domain.filtering import HardFilters
from jobfeed.domain.models import (
    AutoDecayResult,
    JobPosting,
    LLMResponse,
    MLGateResult,
    PipelineRun,
    StageAResult,
)
from jobfeed.personal_ml_learning import PersonalMLStatus
from jobfeed.ports.ml_gate import GateInput
from jobfeed.ports.prompts import PromptBundle
from jobfeed.ports.store_claims import GateCandidate
from jobfeed.services._evaluate_dryrun import DryRunRequest, build_dry_run_preview
from jobfeed.services._evaluate_funnel import _limit_survivors, run_funnel
from jobfeed.services.evaluate import EvaluateService
from jobfeed.services.evaluate_types import (
    EvaluateDependencies,
    EvaluateLLMConfig,
    EvaluateRuntimeConfig,
)

# A clean entry-level SWE JD that clears every MockGate hard-fail rule and is
# long enough (>= SHORT_JD_THRESHOLD = 200 chars) for Stage A to score it.
CLEAN_JD = (
    "Entry-level software engineering role on our platform team. Write Python "
    "and Go code, build backend services, and ship product features end to end. "
    "New grads and early-career engineers are welcome to apply. You will "
    "collaborate with senior engineers, review code, and learn our stack while "
    "contributing to reliable, well-tested services in production from day one."
)
RUN_AT = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
GATE_MAX_CANDIDATES = 123  # distinctive limit asserted on load_gate_candidates


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class FakeStore:
    """In-memory store covering the funnel + Stage A claim/score surface."""

    def __init__(
        self,
        candidates: list[JobPosting],
        *,
        gate_state: dict[str, str | None] | None = None,
        stage_b_candidates: list[JobPosting] | None = None,
        decay_result: AutoDecayResult | None = None,
    ) -> None:
        self._candidates = candidates
        # Stage B dry-run candidates surfaced via load_pending_stage_b; empty
        # unless a "both"/"b" test seeds them.
        self._stage_b_candidates = stage_b_candidates or []
        # Persisted ml_gate_result per job id (None unless seeded); surfaced on
        # GateCandidate so the funnel can skip re-gating already-'pass' reps.
        self._gate_state = gate_state or {}
        self._decay_result = decay_result or AutoDecayResult(ghosted=0, archived=0)
        self.gate_results: list[tuple[str, MLGateResult]] = []
        self.claim_calls: list[list[str]] = []
        self.stage_a_saved: list[str] = []
        self.stage_a_errors: list[tuple[str, str]] = []
        self.stage_b_skipped: list[str] = []
        self.pipeline_runs: list[PipelineRun] = []
        self.lease_calls: list[str] = []
        self.legacy_run_calls: list[str] = []
        self.load_gate_kwargs: list[dict[str, object]] = []
        self.auto_decay_calls: list[dict[str, int]] = []

    async def load_gate_candidates(  # noqa: PLR0913 - mirrors the store port signature
        self,
        *,
        corpus: str = "unrated",
        quality_bands: frozenset[str] | None = None,
        max_days: int | None = None,
        limit: int = 100,
        exclude_gate_failed: bool = True,
        after: tuple[datetime, int] | None = None,
    ) -> list[GateCandidate]:
        """Return preconfigured candidates and record the call kwargs.

        ``exclude_gate_failed`` mirrors the store: when set, rows seeded as
        ``'fail'`` are dropped (matching the SQL predicate). ``after`` is recorded
        but not applied — every funnel test here seeds fewer rows than the page
        size, so the overfetch loop stops after the first (short) page and never
        requests a second; real keyset pagination is covered by the @postgres
        ``test_phase5_evaluate_chain`` suite.
        """
        self.load_gate_kwargs.append(
            {
                "corpus": corpus,
                "quality_bands": quality_bands,
                "max_days": max_days,
                "limit": limit,
                "exclude_gate_failed": exclude_gate_failed,
                "after": after,
            }
        )
        out: list[GateCandidate] = []
        for job in self._candidates:
            state = self._gate_state.get(job.id or "")
            if exclude_gate_failed and state == "fail":
                continue
            out.append(GateCandidate(job=job, ml_gate_result=state))
        return out

    async def claim_stage_a_by_ids(
        self,
        job_ids: list[str],
        *,
        quality_bands: frozenset[str] | None = None,
        corpus: str = "unrated",
        max_days: int | None = None,
        limit: int = 100,
    ) -> list[JobPosting]:
        """Record the claimed id set and return matching candidate rows."""
        del quality_bands, corpus, max_days, limit
        self.claim_calls.append(list(job_ids))
        wanted = set(job_ids)
        return [job for job in self._candidates if job.id in wanted]

    # --- remaining StoreEvaluationClaimMixin surface (unused here) ---

    async def claim_pending_stage_a(self, **_kwargs: object) -> list[JobPosting]:
        """Unused broad Stage A claim (funnel routes through ids)."""
        raise AssertionError("funnel must claim by ids, not broad pending")

    async def preview_claimable_stage_a(self, **_kwargs: object) -> list[JobPosting]:
        """Unused Stage A claim preview."""
        return list(self._candidates)

    async def claim_pending_stage_b(self, **_kwargs: object) -> list[JobPosting]:
        """Unused Stage B claim."""
        return []

    async def load_pending_stage_b(self, **_kwargs: object) -> list[JobPosting]:
        """Return seeded Stage B candidates for dry-run preview (no mutation)."""
        return list(self._stage_b_candidates)

    async def release_stage_a_claim(self, _job_id: str) -> None:
        """Unused Stage A claim release."""

    async def release_stage_b_claim(self, _job_id: str) -> None:
        """Unused Stage B claim release."""

    async def refresh_stage_b_claim(self, _job_id: str) -> None:
        """Unused Stage B claim refresh."""

    async def save_ml_gate_result(self, job_id: str, result: MLGateResult) -> None:
        """Record a persisted gate decision."""
        self.gate_results.append((job_id, result))

    async def save_stage_a(self, job_id: str, _result: StageAResult) -> None:
        """Record a Stage A save."""
        self.stage_a_saved.append(job_id)

    async def save_stage_a_error(self, job_id: str, message: str) -> None:
        """Record a Stage A error."""
        self.stage_a_errors.append((job_id, message))

    async def mark_stage_b_skipped(self, job_id: str) -> None:
        """Record a Stage B skip."""
        self.stage_b_skipped.append(job_id)

    async def record_pipeline_run(self, run: PipelineRun) -> None:
        """Record the persisted pipeline run."""
        self.legacy_run_calls.append("record")
        self.pipeline_runs.append(run)

    async def update_pipeline_run_status(self, _run: object) -> None:
        """No-op status update for testing."""
        self.legacy_run_calls.append("update")

    async def start_run_with_lease(self, run: PipelineRun, **_kwargs: object) -> int:
        """Atomically record the run and return a fencing generation."""
        self.lease_calls.append("start")
        self.pipeline_runs.append(run)
        return 1

    async def renew_run_lease(self, **_kwargs: object) -> bool:
        """Keep the test owner current."""
        self.lease_calls.append("renew")
        return True

    async def finalize_run_with_lease(
        self, _run: PipelineRun, **_kwargs: object
    ) -> bool:
        """Record a fenced terminal transition."""
        self.lease_calls.append("finalize")
        return True

    async def auto_decay(
        self, *, ghost_days: int = 30, archive_ignored_days: int = 14
    ) -> AutoDecayResult:
        """Record the call and return the configured decay result."""
        self.auto_decay_calls.append(
            {"ghost_days": ghost_days, "archive_ignored_days": archive_ignored_days}
        )
        return self._decay_result


class StubPersonalML:
    """Return a fixed learning phase without reading persistence."""

    def __init__(self, state: str) -> None:
        self._state = state

    async def status(self, **_kwargs: object) -> PersonalMLStatus:
        return PersonalMLStatus(
            state=self._state,  # type: ignore[arg-type]
            label_count=150,
            ranking_count=50,
            shadow_count=0,
            next_target=300,
            model_threshold=None,
        )


class StubStoreOps:
    """StoreOps stub with unlimited budget for funnel handoff tests."""

    def __init__(self) -> None:
        self.costs: list[tuple[str, float, int]] = []
        self.usages: list[object] = []

    async def get_cost(self, _day: str) -> None:
        """No prior spend recorded."""

    async def record_cost(self, *, day: str, spent_usd: float, calls: int = 1) -> None:
        """Record a cost mutation."""
        self.costs.append((day, spent_usd, calls))

    async def record_llm_usage_with_cost(
        self, *, day: str, spent_usd: float, usage: object
    ) -> None:
        """Record a usage row."""
        self.costs.append((day, spent_usd, 0))
        self.usages.append(usage)


class StubPromptRenderer:
    """Minimal PromptRenderer for Stage A scoring."""

    def render_stage_a(self, *, resume_text: str, job: JobPosting) -> PromptBundle:
        """Return a skeleton Stage A bundle."""
        del resume_text, job
        return PromptBundle(
            messages=[],
            prompt_hash="ph",
            resume_hash="rh",
        )

    def render_stage_b(
        self, *, resume_text: str, job: JobPosting, stage_a_score: int | None = None
    ) -> PromptBundle:  # pragma: no cover - Stage B unused here
        """Return a skeleton Stage B bundle."""
        del resume_text, job, stage_a_score
        return PromptBundle(messages=[], prompt_hash="ph", resume_hash="rh")


class StageALLM:
    """LLM stub returning a fixed Stage A JSON payload."""

    def __init__(self, score: int = 80) -> None:
        self._score = score

    async def complete(self, _request: object) -> LLMResponse:
        """Return a parseable Stage A response above threshold."""
        content = (
            f'{{"score": {self._score}, "one_line": "ok", '
            '"timing_eligible": "eligible"}'
        )
        return LLMResponse(
            content=content,
            model="mock-model",
            input_tokens=1,
            output_tokens=1,
            cost_usd=0.0,
            cached=False,
            latency_ms=1,
        )


class RecordingLogger:
    """No-op logger."""

    def info(self, _event: str, **_kwargs: object) -> None:
        """Ignore info events."""

    def warning(self, _event: str, **_kwargs: object) -> None:
        """Ignore warning events."""

    def error(self, _event: str, **_kwargs: object) -> None:
        """Ignore error events."""

    def debug(self, _event: str, **_kwargs: object) -> None:
        """Ignore debug events."""


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def _job(
    job_id: str,
    *,
    title: str = "Software Engineer",
    company: str = "Acme",
    location: str = "Remote",
    jd_text: str = CLEAN_JD,
) -> JobPosting:
    """Build a gate-eligible JobPosting with a store id."""
    return JobPosting(
        id=job_id,
        platform="greenhouse",
        canonical_id=f"can-{job_id}",
        url=f"https://example.com/{job_id}",
        title=title,
        company=company,
        location=location,
        discovered_at=RUN_AT,
        jd_text=jd_text,
        jd_quality="full",
        posted_at=RUN_AT,
    )


def _deps(
    store: FakeStore,
    *,
    gate: MockGate | None,
    filters: HardFilters | None,
    personal_ml: StubPersonalML | None = None,
):
    """Build EvaluateDependencies wired to fakes."""
    return EvaluateDependencies(
        store=store,  # type: ignore[arg-type]
        store_ops=StubStoreOps(),  # type: ignore[arg-type]
        store_status=store,  # type: ignore[arg-type]
        prompt_renderer=StubPromptRenderer(),  # type: ignore[arg-type]
        llm_stage_a=StageALLM(),  # type: ignore[arg-type]
        llm_stage_b=StageALLM(),  # type: ignore[arg-type]
        ml_gate=gate,
        hard_filters=filters,
        personal_ml=personal_ml,  # type: ignore[arg-type]
    )


def _config(*, ml_gate_enabled: bool) -> EvaluateRuntimeConfig:
    """Build EvaluateRuntimeConfig with gate flag and tiny budget room."""
    return EvaluateRuntimeConfig(
        llm=EvaluateLLMConfig(
            stage_a="mock-a",
            stage_b="mock-b",
            max_concurrent=4,
            max_daily_score_calls=1000,
            max_daily_cost_usd=100.0,
        ),
        stage_a_threshold=60,
        resume_text="resume",
        ml_gate_enabled=ml_gate_enabled,
        ml_gate_max_candidates=GATE_MAX_CANDIDATES,
    )


def _run() -> PipelineRun:
    """Build a fresh PipelineRun for funnel assertions."""
    return PipelineRun(run_id="run-1", started_at=RUN_AT, source="evaluate")


# ---------------------------------------------------------------------------
# Funnel-level tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_load_uses_max_candidates_and_gate_exclude_flag() -> None:
    """Load passes max_candidates as limit and ml_gate_enabled as exclude flag."""
    store = FakeStore([_job("a")])
    deps = _deps(store, gate=MockGate(), filters=None)
    config = _config(ml_gate_enabled=True)

    await run_funnel(
        deps,
        config,
        _run(),
        "unrated",
        None,
        logger=RecordingLogger(),  # type: ignore[arg-type]
        dry_run=False,
    )

    kwargs = store.load_gate_kwargs[0]
    assert kwargs["limit"] == GATE_MAX_CANDIDATES
    assert kwargs["exclude_gate_failed"] is True
    assert kwargs["quality_bands"] == frozenset({"full", "good"})


@pytest.mark.asyncio
async def test_hard_filter_drops_are_excluded_and_counted() -> None:
    """Hard-filter-blocked candidates are dropped and counted, not gated."""
    keep = _job("keep")
    blocked = _job("blocked", company="BlockedCo")
    store = FakeStore([keep, blocked])
    filters = HardFilters(company_blocklist=["BlockedCo"])
    deps = _deps(store, gate=MockGate(), filters=filters)
    run = _run()

    survivors = await run_funnel(
        deps,
        _config(ml_gate_enabled=True),
        run,
        "unrated",
        None,
        logger=RecordingLogger(),  # type: ignore[arg-type]
        dry_run=False,
    )

    assert survivors == ["keep"]
    assert run.jobs_filtered == 1
    # The blocked job is never gated.
    assert [jid for jid, _ in store.gate_results] == ["keep"]


@pytest.mark.asyncio
async def test_none_hard_filters_treated_as_no_drops() -> None:
    """deps.hard_filters is None => zero filtered, all survive to gate."""
    store = FakeStore([_job("a"), _job("b")])
    # Distinct twin keys so both are representatives.
    store._candidates[1] = replace(store._candidates[1], title="Backend Engineer")
    deps = _deps(store, gate=MockGate(), filters=None)
    run = _run()

    survivors = await run_funnel(
        deps,
        _config(ml_gate_enabled=True),
        run,
        "unrated",
        None,
        logger=RecordingLogger(),  # type: ignore[arg-type]
        dry_run=False,
    )

    assert run.jobs_filtered == 0
    assert set(survivors) == {"a", "b"}


@pytest.mark.asyncio
async def test_only_representative_is_gated_and_survives() -> None:
    """Twins fold to one representative; non-reps are never gated."""
    # Same (company, title) => one twin cluster. ATS platform + later posted_at
    # makes "rep" the representative over the linkedin twin.
    rep = _job("rep")
    twin = replace(
        _job("twin"),
        platform="linkedin",
        posted_at=datetime(2026, 5, 1, tzinfo=UTC),
    )
    store = FakeStore([rep, twin])
    deps = _deps(store, gate=MockGate(), filters=None)
    run = _run()

    survivors = await run_funnel(
        deps,
        _config(ml_gate_enabled=True),
        run,
        "unrated",
        None,
        logger=RecordingLogger(),  # type: ignore[arg-type]
        dry_run=False,
    )

    assert survivors == ["rep"]
    assert [jid for jid, _ in store.gate_results] == ["rep"]
    assert run.jobs_ml_gated == 0


@pytest.mark.asyncio
async def test_gate_failed_reps_persisted_and_excluded() -> None:
    """Gate-failed reps are persisted and excluded; jobs_ml_gated counts them."""
    passing = _job("pass", title="Software Engineer", company="PassCo")
    # A non-SWE posting hard-fails inside MockGate regardless of default_result.
    failing = _job(
        "fail",
        title="Sales Manager",
        company="FailCo",
        jd_text="Sell our products to enterprise clients. Close deals.",
    )
    store = FakeStore([passing, failing])
    deps = _deps(store, gate=MockGate(), filters=None)
    run = _run()

    survivors = await run_funnel(
        deps,
        _config(ml_gate_enabled=True),
        run,
        "unrated",
        None,
        logger=RecordingLogger(),  # type: ignore[arg-type]
        dry_run=False,
    )

    assert survivors == ["pass"]
    # Both reps were gated + persisted; only the failing one counts.
    persisted = {jid: res.result for jid, res in store.gate_results}
    assert persisted == {"pass": "pass", "fail": "fail"}
    assert run.jobs_ml_gated == 1


@pytest.mark.asyncio
async def test_active_gate_explores_ten_percent_of_failures_for_future_recall() -> None:
    """A deterministic 10% failure sample still receives its Quick teacher label."""
    exploration = _job(
        "10",
        title="Sales Manager",
        company="ExploreCo",
        jd_text="Sell our products to enterprise clients.",
    )
    store = FakeStore([exploration])
    deps = _deps(store, gate=MockGate(), filters=None)
    run = _run()

    survivors = await run_funnel(
        deps,
        _config(ml_gate_enabled=True),
        run,
        "unrated",
        None,
        logger=RecordingLogger(),  # type: ignore[arg-type]
        dry_run=False,
    )

    assert survivors == ["10"]
    assert store.gate_results[0][1].result == "fail"
    assert run.jobs_ml_gated == 0


@pytest.mark.asyncio
async def test_already_pass_rep_survives_without_regating() -> None:
    """A rep persisted 'pass' survives WITHOUT being re-gated or re-persisted.

    Even with a gate configured to FAIL everything, the already-'pass' rep is
    handed through to scoring (never sent to predict_batch / save), proving a
    model/threshold swap cannot silently drop a previously-passed row.
    """
    rep = _job("already-pass")
    store = FakeStore([rep], gate_state={"already-pass": "pass"})
    # default_result='fail' would flip the rep to 'fail' if it were re-gated.
    deps = _deps(store, gate=MockGate(default_result="fail"), filters=None)
    run = _run()

    survivors = await run_funnel(
        deps,
        _config(ml_gate_enabled=True),
        run,
        "unrated",
        None,
        logger=RecordingLogger(),  # type: ignore[arg-type]
        dry_run=False,
    )

    assert survivors == ["already-pass"]
    assert store.gate_results == []  # never re-gated / re-persisted
    assert run.jobs_ml_gated == 0  # already-pass is not counted


@pytest.mark.asyncio
async def test_mixed_null_and_pass_reps_only_null_gated() -> None:
    """Only NULL-gate reps are gated; already-'pass' reps pass through."""
    null_rep = _job("null", company="NullCo")
    pass_rep = _job("pass", company="PassCo")
    store = FakeStore([null_rep, pass_rep], gate_state={"pass": "pass"})
    deps = _deps(store, gate=MockGate(default_result="pass"), filters=None)
    run = _run()

    survivors = await run_funnel(
        deps,
        _config(ml_gate_enabled=True),
        run,
        "unrated",
        None,
        logger=RecordingLogger(),  # type: ignore[arg-type]
        dry_run=False,
    )

    assert set(survivors) == {"null", "pass"}
    # Only the NULL-gate rep was gated + persisted.
    assert [jid for jid, _ in store.gate_results] == ["null"]
    assert run.jobs_ml_gated == 0


@pytest.mark.asyncio
async def test_dry_run_slices_preview_to_limit_in_claim_order() -> None:
    """Dry-run previews only `limit` survivors, newest discovered_at first."""
    # Three distinct-title reps, ascending discovered_at; claim order is newest
    # first => job-2 (newest), job-1, then job-0.
    jobs = [
        replace(
            _job(f"job-{i}", company=f"Co{i}"),
            discovered_at=datetime(2026, 6, 1, 12, i, tzinfo=UTC),
        )
        for i in range(3)
    ]
    store = FakeStore(jobs)
    deps = _deps(store, gate=MockGate(), filters=None)
    run = _run()

    await run_funnel(
        deps,
        _config(ml_gate_enabled=True),
        run,
        "unrated",
        None,
        logger=RecordingLogger(),  # type: ignore[arg-type]
        dry_run=True,
        limit=2,
    )

    assert [item.job_id for item in run.dry_run_preview] == ["job-2", "job-1"]


@pytest.mark.asyncio
async def test_gate_disabled_skips_gating() -> None:
    """ml_gate_enabled=False => no gating, all reps survive, count unchanged."""
    store = FakeStore([_job("a"), replace(_job("b"), title="Data Engineer")])
    deps = _deps(store, gate=MockGate(default_result="fail"), filters=None)
    run = _run()

    survivors = await run_funnel(
        deps,
        _config(ml_gate_enabled=False),
        run,
        "unrated",
        None,
        logger=RecordingLogger(),  # type: ignore[arg-type]
        dry_run=False,
    )

    assert set(survivors) == {"a", "b"}
    assert store.gate_results == []
    assert run.jobs_ml_gated == 0


@pytest.mark.asyncio
async def test_ranking_phase_scores_in_shadow_without_filtering() -> None:
    """The shared gate teaches a user threshold but cannot hide ranking jobs."""
    passing = _job("pass", company="PassCo")
    failing = _job(
        "fail",
        title="Sales Manager",
        company="FailCo",
        jd_text="Sell our products to enterprise clients.",
    )
    store = FakeStore([passing, failing])
    deps = _deps(
        store,
        gate=MockGate(),
        filters=None,
        personal_ml=StubPersonalML("ranking"),
    )
    run = _run()

    survivors = await run_funnel(
        deps,
        _config(ml_gate_enabled=False),
        run,
        "unrated",
        None,
        logger=RecordingLogger(),  # type: ignore[arg-type]
        dry_run=False,
    )

    assert set(survivors) == {"pass", "fail"}
    assert {job_id for job_id, _result in store.gate_results} == {"pass", "fail"}
    assert run.jobs_ml_gated == 0


@pytest.mark.asyncio
async def test_paused_model_restores_previously_rejected_candidates() -> None:
    """Recall drift disables persisted gate failures before the candidate query."""
    candidate = _job("previously-failed")
    store = FakeStore([candidate], gate_state={"previously-failed": "fail"})
    deps = _deps(
        store,
        gate=MockGate(),
        filters=None,
        personal_ml=StubPersonalML("paused"),
    )

    survivors = await run_funnel(
        deps,
        _config(ml_gate_enabled=True),
        _run(),
        "unrated",
        None,
        logger=RecordingLogger(),  # type: ignore[arg-type]
        dry_run=False,
    )

    assert survivors == ["previously-failed"]
    assert store.load_gate_kwargs[0]["exclude_gate_failed"] is False


@pytest.mark.asyncio
async def test_gate_none_skips_gating_even_when_enabled() -> None:
    """ml_gate is None => gating skipped even if ml_gate_enabled=True."""
    store = FakeStore([_job("a")])
    deps = _deps(store, gate=None, filters=None)
    run = _run()

    survivors = await run_funnel(
        deps,
        _config(ml_gate_enabled=True),
        run,
        "unrated",
        None,
        logger=RecordingLogger(),  # type: ignore[arg-type]
        dry_run=False,
    )

    assert survivors == ["a"]
    assert store.gate_results == []
    assert run.jobs_ml_gated == 0


@pytest.mark.asyncio
async def test_dry_run_persists_nothing_and_previews_survivors() -> None:
    """Dry-run persists no gate rows and records a survivor preview."""
    store = FakeStore([_job("a"), replace(_job("b"), title="Platform Engineer")])
    deps = _deps(store, gate=MockGate(), filters=None)
    run = _run()

    survivors = await run_funnel(
        deps,
        _config(ml_gate_enabled=True),
        run,
        "unrated",
        None,
        logger=RecordingLogger(),  # type: ignore[arg-type]
        dry_run=True,
    )

    assert survivors == []  # dry-run returns no ids; preview is recorded instead
    assert store.gate_results == []  # no persistence in dry-run
    assert {item.job_id for item in run.dry_run_preview} == {"a", "b"}
    assert all(item.stage == "stage_a" for item in run.dry_run_preview)


# ---------------------------------------------------------------------------
# EvaluateService handoff tests
# ---------------------------------------------------------------------------


def _service(store: FakeStore, *, gate: MockGate | None, ml_gate_enabled: bool):
    """Build an EvaluateService wired to fakes."""
    return EvaluateService(
        deps=_deps(store, gate=gate, filters=None),
        config=_config(ml_gate_enabled=ml_gate_enabled),
        logger=RecordingLogger(),  # type: ignore[arg-type]
    )


@pytest.mark.asyncio
async def test_survivor_ids_handed_to_claim_stage_a_by_ids() -> None:
    """Stage A claims exactly the funnel survivor ids."""
    passing = _job("pass", company="PassCo")
    failing = _job(
        "fail",
        title="Sales Manager",
        company="FailCo",
        jd_text="Sell our products. Close deals with clients.",
    )
    store = FakeStore([passing, failing])
    service = _service(store, gate=MockGate(), ml_gate_enabled=True)

    run = await service.run(stage="a", corpus="unrated")

    assert store.claim_calls == [["pass"]]
    assert store.stage_a_saved == ["pass"]
    assert run.jobs_ml_gated == 1
    assert run.stage_a_scored == 1
    assert store.lease_calls == ["start", "finalize"]
    assert store.legacy_run_calls == []


@pytest.mark.asyncio
async def test_empty_survivor_set_claims_nothing() -> None:
    """No survivors => claim_stage_a_by_ids never called, nothing scored."""
    failing = _job(
        "fail",
        title="Sales Manager",
        jd_text="Sell our products. Close deals with clients.",
    )
    store = FakeStore([failing])
    service = _service(store, gate=MockGate(), ml_gate_enabled=True)

    run = await service.run(stage="a", corpus="unrated")

    assert store.claim_calls == []
    assert store.stage_a_saved == []
    assert run.stage_a_scored == 0


@pytest.mark.asyncio
async def test_gate_disabled_path_scores_dedup_survivors() -> None:
    """With the gate off, hard-filter+dedupe survivors are scored via claim-by-id."""
    store = FakeStore([_job("a"), replace(_job("b"), title="Data Engineer")])
    service = _service(store, gate=None, ml_gate_enabled=False)

    run = await service.run(stage="a", corpus="unrated")

    assert store.claim_calls and set(store.claim_calls[0]) == {"a", "b"}
    assert set(store.stage_a_saved) == {"a", "b"}
    assert run.jobs_ml_gated == 0


@pytest.mark.asyncio
async def test_dry_run_service_does_not_persist_or_score() -> None:
    """A dry-run service run previews survivors without claiming or scoring."""
    store = FakeStore([_job("a"), replace(_job("b"), title="Data Engineer")])
    service = _service(store, gate=MockGate(), ml_gate_enabled=True)

    run = await service.run(stage="a", corpus="unrated", dry_run=True)

    assert store.claim_calls == []
    assert store.stage_a_saved == []
    assert store.gate_results == []
    assert store.pipeline_runs == []  # dry-run does not record the run
    assert store.lease_calls == []
    assert store.legacy_run_calls == []
    assert {item.job_id for item in run.dry_run_preview} == {"a", "b"}


@pytest.mark.asyncio
async def test_dry_run_stage_both_previews_stage_a_and_stage_b() -> None:
    """A ``stage="both"`` dry-run previews BOTH Stage A survivors and Stage B.

    Seeds Stage A funnel survivors (distinct titles => both representatives) and
    a separate Stage B candidate. The preview must carry both stages: the Stage A
    items appended by ``run_funnel`` (proving Fix A's ``.extend`` keeps the first
    pass) plus the Stage B item appended after. Nothing is claimed or persisted.
    """
    stage_a_jobs = [_job("a"), replace(_job("b"), title="Data Engineer")]
    stage_b_job = replace(
        _job("b-cand", title="Platform Engineer", company="StageBCo"),
    )
    store = FakeStore(stage_a_jobs, stage_b_candidates=[stage_b_job])
    service = _service(store, gate=MockGate(), ml_gate_enabled=True)

    run = await service.run(stage="both", corpus="unrated", dry_run=True)

    stages = {item.stage for item in run.dry_run_preview}
    assert stages == {"stage_a", "stage_b"}
    stage_a_ids = {i.job_id for i in run.dry_run_preview if i.stage == "stage_a"}
    stage_b_ids = {i.job_id for i in run.dry_run_preview if i.stage == "stage_b"}
    assert stage_a_ids == {"a", "b"}  # Fix A: first (Stage A) pass survives
    assert stage_b_ids == {"b-cand"}
    # Dry-run mutates nothing.
    assert store.claim_calls == []
    assert store.gate_results == []
    assert store.pipeline_runs == []


def test_limit_survivors_none_vs_zero_vs_positive() -> None:
    """``None`` keeps all survivors; ``<=0`` drops all; ``>0`` slices in order."""
    jobs = [
        replace(
            _job(f"job-{i}", company=f"Co{i}"),
            discovered_at=datetime(2026, 6, 1, 12, i, tzinfo=UTC),
        )
        for i in range(3)
    ]

    assert _limit_survivors(jobs, None) == jobs  # only None is unsliced
    assert _limit_survivors(jobs, 0) == []  # 0 means zero, not unlimited
    assert _limit_survivors(jobs, -5) == []  # any non-positive => empty
    # Positive slices newest-discovered-first (claim order), unchanged behaviour.
    assert [j.id for j in _limit_survivors(jobs, 2)] == ["job-2", "job-1"]


@pytest.mark.asyncio
async def test_real_run_limit_zero_gates_scores_persists_nothing() -> None:
    """A real Stage A run with limit=0 does NO funnel work: gate/score/claim 0.

    ``--limit 0`` is the "max jobs"=0 contract: zero, not unlimited. The Stage A
    early guard must skip the funnel entirely so nothing is gated, persisted, or
    claimed — even with eligible survivors and an enabled gate.
    """
    store = FakeStore([_job("a"), replace(_job("b"), title="Data Engineer")])
    service = _service(store, gate=MockGate(), ml_gate_enabled=True)

    run = await service.run(stage="a", corpus="unrated", limit=0)

    assert run.jobs_ml_gated == 0
    assert store.gate_results == []  # nothing gated/persisted (no save_ml_gate_result)
    assert store.claim_calls == []  # nothing claimed
    assert store.stage_a_saved == []
    assert run.stage_a_scored == 0
    assert store.load_gate_kwargs == []  # funnel never even loaded candidates


@pytest.mark.asyncio
async def test_dry_run_limit_zero_previews_empty_survivor_set() -> None:
    """A dry-run with limit=0 previews an EMPTY survivor set (zero, not all)."""
    store = FakeStore([_job("a"), replace(_job("b"), title="Data Engineer")])
    deps = _deps(store, gate=MockGate(), filters=None)
    run = _run()

    await run_funnel(
        deps,
        _config(ml_gate_enabled=True),
        run,
        "unrated",
        None,
        logger=RecordingLogger(),  # type: ignore[arg-type]
        dry_run=True,
        limit=0,
    )

    assert run.dry_run_preview == []  # limit=0 => preview nothing


@pytest.mark.asyncio
async def test_dry_run_service_limit_zero_previews_empty() -> None:
    """End-to-end dry-run service path with limit=0 yields an empty preview."""
    store = FakeStore([_job("a"), replace(_job("b"), title="Data Engineer")])
    service = _service(store, gate=MockGate(), ml_gate_enabled=True)

    run = await service.run(stage="a", corpus="unrated", limit=0, dry_run=True)

    assert run.dry_run_preview == []
    assert store.gate_results == []
    assert store.claim_calls == []


class _CountingGate(MockGate):
    """MockGate that counts predict_batch invocations (Fix A guard proof)."""

    def __init__(self) -> None:
        super().__init__()
        self.predict_calls = 0

    async def predict_batch(self, jobs: list[GateInput]) -> list[MLGateResult]:
        """Count the call, then delegate to the deterministic MockGate."""
        self.predict_calls += 1
        return await super().predict_batch(jobs)


@pytest.mark.asyncio
async def test_dry_run_stage_a_limit_zero_skips_load_and_gate() -> None:
    """Dry-run Stage A with limit=0 loads NO candidates and gates nothing.

    The fix short-circuits ``build_dry_run_preview`` before ``run_funnel``, so
    the heavy embedder (``predict_batch``) is never triggered just to slice the
    preview to ``[]`` afterwards. Asserts: no candidate load, no predict_batch,
    no persistence, empty preview.
    """
    store = FakeStore([_job("a"), replace(_job("b"), title="Data Engineer")])
    gate = _CountingGate()
    deps = _deps(store, gate=gate, filters=None)
    run = _run()
    request = DryRunRequest(RecordingLogger(), "a", "unrated", 0, None)

    await build_dry_run_preview(deps, _config(ml_gate_enabled=True), run, request)

    assert store.load_gate_kwargs == []  # funnel never loaded candidates
    assert gate.predict_calls == 0  # embedder/predict never triggered
    assert store.gate_results == []  # nothing persisted
    assert run.dry_run_preview == []  # empty preview, zero not all


@pytest.mark.asyncio
async def test_dry_run_stage_b_limit_zero_skips_preview_load() -> None:
    """Dry-run Stage B with limit=0 loads NO Stage B candidates (mirrors live).

    Live ``_run_stage_b`` early-returns on ``limit <= 0``; the dry-run preview
    must match. Stage B candidates are seeded here, so a non-empty preview would
    mean the ``limit > 0`` guard on the Stage B branch is missing.
    """
    store = FakeStore([], stage_b_candidates=[_job("b1")])
    deps = _deps(store, gate=MockGate(), filters=None)
    run = _run()
    request = DryRunRequest(RecordingLogger(), "b", "unrated", 0, None)

    await build_dry_run_preview(deps, _config(ml_gate_enabled=True), run, request)

    assert run.dry_run_preview == []  # Stage B preview skipped at limit=0


# ---------------------------------------------------------------------------
# Auto-decay wiring tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_auto_decay_called_before_stage_a() -> None:
    """auto_decay runs BEFORE Stage A scoring, with config defaults (30/14)."""
    store = FakeStore([_job("a")])
    service = _service(store, gate=MockGate(), ml_gate_enabled=True)

    run = await service.run(stage="a", corpus="unrated")

    # auto_decay was called exactly once, before any scoring
    assert len(store.auto_decay_calls) == 1
    assert store.auto_decay_calls[0] == {
        "ghost_days": 30,
        "archive_ignored_days": 14,
    }
    # Stage A still scored after decay
    assert run.stage_a_scored >= 1


@pytest.mark.asyncio
async def test_auto_decay_called_with_custom_thresholds() -> None:
    """auto_decay uses ghost_days and archive_ignored_days from config."""
    store = FakeStore([_job("a")])
    config = EvaluateRuntimeConfig(
        llm=EvaluateLLMConfig(
            stage_a="mock-a",
            stage_b="mock-b",
            max_concurrent=4,
            max_daily_score_calls=1000,
            max_daily_cost_usd=100.0,
        ),
        stage_a_threshold=60,
        resume_text="resume",
        ml_gate_enabled=True,
        ml_gate_max_candidates=GATE_MAX_CANDIDATES,
        ghost_days=45,
        archive_ignored_days=7,
    )
    service = EvaluateService(
        deps=_deps(store, gate=MockGate(), filters=None),
        config=config,
        logger=RecordingLogger(),  # type: ignore[arg-type]
    )

    await service.run(stage="a", corpus="unrated")

    assert store.auto_decay_calls[0] == {
        "ghost_days": 45,
        "archive_ignored_days": 7,
    }


@pytest.mark.asyncio
async def test_auto_decay_with_nonzero_results_logs() -> None:
    """When decay transitions jobs, the service completes normally."""
    decay = AutoDecayResult(ghosted=3, archived=1)
    store = FakeStore([_job("a")], decay_result=decay)
    service = _service(store, gate=MockGate(), ml_gate_enabled=True)

    run = await service.run(stage="a", corpus="unrated")

    assert len(store.auto_decay_calls) == 1
    # Service still ran Stage A after decay
    assert run.stage_a_scored >= 1


@pytest.mark.asyncio
async def test_auto_decay_skipped_on_dry_run() -> None:
    """auto_decay must NOT run in dry-run mode to avoid mutating the DB."""
    store = FakeStore([_job("a")])
    service = _service(store, gate=MockGate(), ml_gate_enabled=True)

    await service.run(stage="a", corpus="unrated", dry_run=True)

    assert len(store.auto_decay_calls) == 0


@pytest.mark.asyncio
async def test_auto_decay_called_for_stage_b_only() -> None:
    """auto_decay runs even when only Stage B is requested."""
    store = FakeStore([])
    service = _service(store, gate=MockGate(), ml_gate_enabled=True)

    await service.run(stage="b", corpus="unrated")

    assert len(store.auto_decay_calls) == 1


def test_evaluate_service_imports_no_config() -> None:
    """The evaluate service module must not import jobfeed.config."""
    from pathlib import Path  # noqa: PLC0415

    source_file = Path(__file__).resolve().parents[2] / (
        "src/jobfeed/services/evaluate.py"
    )
    source = source_file.read_text()
    assert "jobfeed.config" not in source, "evaluate.py must not import jobfeed.config"


def test_numeric_job_ids_coerces_valid_and_skips_malformed() -> None:
    """Plain/signed bigint ids coerce; malformed ids are skipped, never raise.

    ``int(jid)`` under ``try/except`` is the robust guard: ``'--1'`` and
    ``'1-2'`` would pass a naive ``lstrip('-').isdigit()`` check yet crash
    ``int()``, aborting the whole ``claim_stage_a_by_ids`` batch. Here they are
    dropped instead, leaving only the real store ids.
    """
    mixed = _numeric_job_ids(["1", "--1", "1-2", "abc", "2", "-3"])

    assert mixed == [1, 2, -3]  # signed bigint coerces; malformed dropped


def test_numeric_job_ids_all_malformed_returns_empty() -> None:
    """An all-malformed/non-numeric list yields ``[]`` (claim short-circuits)."""
    assert _numeric_job_ids(["--1", "abc", "1-2", ""]) == []
