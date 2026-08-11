"""Phase 5 evaluation-persistence contract.

Locks the persisted shape of every evaluation artifact written by the funnel +
Stage A + Stage B: the ML-gate decision/feature columns on ``jobs`` and the
Stage A / Stage B result columns on ``evaluations``. Two complementary locks:

1. **Domain shape** — every persisted field is enumerated and type-asserted on
   frozen literal fixtures built through the real domain models / ``MockGate``
   (no embedder, no PG). ``clearance_required`` / ``school_restricted`` are pinned
   as ``bool`` — the int->bool coercion boundary the gate adapter performs.
2. **Persisted column names** — the live SQL of ``save_ml_gate_result`` /
   ``save_stage_a`` / ``save_stage_b`` is introspected so renaming any column
   turns this contract RED. The current name ``ml_gate_fail_reason`` is pinned
   explicitly and the spec's older ``ml_gate_hard_fail_reason`` is asserted gone.

This runs in ``make quality`` (no PostgreSQL, no model download): it never opens
a connection and never loads the real gate / fastembed embedder.
"""

from __future__ import annotations

import asyncio
import inspect

from jobfeed.adapters.ml.mock import MockGate
from jobfeed.adapters.store.postgres import PostgresStore
from jobfeed.domain.models import (
    FitAnalysis,
    GapItem,
    MatchItem,
    MLGateResult,
    StageAResult,
    StageBResult,
    Verdict,
)
from jobfeed.ports.ml_gate import GateInput

# ── frozen JD fixtures ────────────────────────────────────────────────────────
# A "pass" posting that still trips the clearance + school feature columns via an
# *obtainable* clearance (so it does NOT hard-fail) — this is the int->bool
# coercion boundary. "security clearance" also lights the `security` domain tag.
_PASS_TITLE = "Software Engineer Intern"
_PASS_JD = (
    "We are hiring a Software Engineer to build backend services. "
    "You must be enrolled at MIT. You must be eligible to obtain a security "
    "clearance. Requires a Bachelor degree. We use Python and Go and "
    "Kubernetes. This is an internship in the robotics domain."
)
# A non-SWE posting that the real hard-fail rules reject regardless of model.
_HARD_FAIL_TITLE = "Mechanical Design Engineer"
_HARD_FAIL_JD = (
    "Design mechanical assemblies and tolerances for manufacturing lines. "
    "Owns CAD models and thermal analysis. No software work."
)

# Frozen expected feature values for the pass posting (rule-extracted, pinned).
_EXPECTED_PASS_FEATURES: dict[str, object] = {
    "is_swe_role": True,
    "seniority_level": "entry",
    "degree_required": "bachelors",
    "clearance_required": True,
    "school_restricted": True,
    "yoe_min": None,
    "domain_tags": ["robotics", "security"],
    "tech_required": ["python", "go", "kubernetes"],
    "role_type": "intern",
}


def _gate(title: str, jd: str, *, default_result: str = "pass") -> MLGateResult:
    """Run MockGate on one posting and return its single MLGateResult."""
    gate = MockGate(default_result=default_result)
    results = asyncio.run(
        gate.predict_batch([GateInput(job_id="1", title=title, jd_text=jd)])
    )
    return results[0]


def _stage_a_fixture() -> StageAResult:
    """Frozen Stage A result; a field rename/retire breaks construction here."""
    return StageAResult(
        score=75,
        one_line="Strong backend fit.",
        timing_eligible="eligible",
        model="mock/stage-a",
        prompt_hash="pa-hash",
        resume_hash="ra-hash",
        cost_usd=0.0,
    )


def _stage_b_fixture() -> StageBResult:
    """Frozen Stage B result with raw_blocks; a rename breaks construction."""
    return StageBResult(
        verdict=Verdict.CONSIDER,
        jd_summary="Backend platform role.",
        fit_analysis=FitAnalysis(
            score=72,
            strengths=[
                MatchItem(requirement="Python", evidence="Production Python work.")
            ],
            gaps=[
                GapItem(
                    requirement="Company systems",
                    severity="minor",
                    mitigation="Connect prior automation.",
                )
            ],
        ),
        resume_hooks=["Mention the local-first pipeline."],
        model="mock/stage-b",
        prompt_hash="pb-hash",
        resume_hash="rb-hash",
        cost_usd=0.0,
        raw_blocks={"verdict": {}, "jd_summary": {}},
    )


# ── ML-gate domain-shape contract ─────────────────────────────────────────────


def test_ml_gate_pass_result_enumerates_every_persisted_field() -> None:
    """Every persisted ML-gate field is present, typed, and value-pinned."""
    result = _gate(_PASS_TITLE, _PASS_JD)

    # Decision columns.
    assert isinstance(result.score, float)
    assert result.score == 1.0
    assert result.result == "pass"
    assert result.fail_reason is None
    assert result.version == "mock"

    # The 9 feature columns, by exact attribute name + type + value.
    actual = {
        "is_swe_role": result.is_swe_role,
        "seniority_level": result.seniority_level,
        "degree_required": result.degree_required,
        "clearance_required": result.clearance_required,
        "school_restricted": result.school_restricted,
        "yoe_min": result.yoe_min,
        "domain_tags": result.domain_tags,
        "tech_required": result.tech_required,
        "role_type": result.role_type,
    }
    assert actual == _EXPECTED_PASS_FEATURES


def test_ml_gate_clearance_and_school_are_bool_not_int() -> None:
    """clearance_required / school_restricted cross the int->bool boundary."""
    result = _gate(_PASS_TITLE, _PASS_JD)

    # Pinned as real bools — `1`/`0` ints (legacy feature dtype) would be a
    # silent regression of the gate adapter's coercion, so reject `int`.
    assert result.clearance_required is True
    assert result.school_restricted is True
    assert isinstance(result.clearance_required, bool)
    assert isinstance(result.school_restricted, bool)
    assert not isinstance(result.is_swe_role, int) or isinstance(
        result.is_swe_role, bool
    )
    assert isinstance(result.is_swe_role, bool)


def test_ml_gate_result_field_set_is_frozen() -> None:
    """The MLGateResult field set is exactly the persisted column contract."""
    expected = {
        "score",
        "result",
        "fail_reason",
        "version",
        "is_swe_role",
        "seniority_level",
        "degree_required",
        "clearance_required",
        "school_restricted",
        "yoe_min",
        "domain_tags",
        "tech_required",
        "role_type",
    }
    actual = set(MLGateResult.__dataclass_fields__)
    assert actual == expected


def test_ml_gate_hard_fail_carries_reason_string() -> None:
    """A hard-failing role persists result='fail' + a non-empty reason string."""
    result = _gate(_HARD_FAIL_TITLE, _HARD_FAIL_JD)

    assert result.result == "fail"
    assert isinstance(result.fail_reason, str)
    assert result.fail_reason == "not software engineering role"
    assert result.score == 0.0
    assert result.is_swe_role is False


# ── Stage A domain-shape contract ─────────────────────────────────────────────


def test_stage_a_result_enumerates_every_persisted_field() -> None:
    """Stage A persists score, one_line, timing_eligible, model, both hashes."""
    result = _stage_a_fixture()

    assert isinstance(result.score, int)
    assert result.score == 75
    assert result.one_line == "Strong backend fit."
    assert result.timing_eligible == "eligible"
    assert result.model == "mock/stage-a"
    assert result.prompt_hash == "pa-hash"
    assert result.resume_hash == "ra-hash"
    assert result.cost_usd == 0.0


def test_stage_a_result_field_set_is_frozen() -> None:
    """The StageAResult field set is exactly the persisted column contract."""
    assert set(StageAResult.__dataclass_fields__) == {
        "score",
        "one_line",
        "timing_eligible",
        "model",
        "prompt_hash",
        "resume_hash",
        "cost_usd",
    }


# ── Stage B domain-shape contract ─────────────────────────────────────────────


def test_stage_b_result_enumerates_every_persisted_field() -> None:
    """Stage B persists verdict, jd_summary, fit_analysis, hooks, raw, hashes."""
    result = _stage_b_fixture()

    assert isinstance(result.verdict, Verdict)
    assert result.verdict == Verdict.CONSIDER
    assert result.jd_summary == "Backend platform role."
    assert isinstance(result.fit_analysis, FitAnalysis)
    assert result.fit_analysis.score == 72
    assert result.fit_analysis.strengths[0].requirement == "Python"
    assert result.fit_analysis.gaps[0].severity == "minor"
    assert result.resume_hooks == ["Mention the local-first pipeline."]
    assert result.model == "mock/stage-b"
    assert result.prompt_hash == "pb-hash"
    assert result.resume_hash == "rb-hash"
    assert result.cost_usd == 0.0
    assert isinstance(result.raw_blocks, dict)


def test_stage_b_result_field_set_is_frozen() -> None:
    """The StageBResult field set is exactly the persisted column contract."""
    assert set(StageBResult.__dataclass_fields__) == {
        "verdict",
        "jd_summary",
        "fit_analysis",
        "resume_hooks",
        "model",
        "prompt_hash",
        "resume_hash",
        "cost_usd",
        "raw_blocks",
    }


# ── persisted column-name contract (SQL introspection, no DB) ─────────────────
# These pin the *stored* column names so a rename in the writer turns RED, even
# though the writes themselves require PG. Reading the method source is free.

_GATE_SQL = inspect.getsource(PostgresStore.save_ml_gate_result)
_STAGE_A_SQL = inspect.getsource(PostgresStore.save_stage_a)
_STAGE_B_SQL = inspect.getsource(PostgresStore.save_stage_b)
_TOP_EVALUATED_SQL = " ".join(
    inspect.getsource(PostgresStore.top_evaluated_jobs).split()
)


def test_persisted_ml_gate_columns_are_pinned() -> None:
    """save_ml_gate_result writes exactly the locked ml_gate / feature columns."""
    for column in (
        "ml_gate_score",
        "ml_gate_result",
        "ml_gate_fail_reason",
        "ml_gate_version",
        "is_swe_role",
        "seniority_level",
        "degree_required",
        "clearance_required",
        "school_restricted",
        "yoe_min",
        "domain_tags",
        "tech_required",
        "role_type",
    ):
        assert column in _GATE_SQL, f"persisted ml_gate column renamed: {column}"


def test_ml_gate_fail_reason_column_name_is_pinned() -> None:
    """The reason column is ``ml_gate_fail_reason`` — NOT the older spec name."""
    assert "ml_gate_fail_reason" in _GATE_SQL
    assert "ml_gate_hard_fail_reason" not in _GATE_SQL


def test_persisted_stage_a_columns_are_pinned() -> None:
    """save_stage_a writes exactly the locked Stage A columns."""
    for column in (
        "stage_a_score",
        "stage_a_one_line",
        "stage_a_timing_eligible",
        "stage_a_model",
        "stage_a_cost_usd",
        "stage_a_prompt_hash",
        "stage_a_resume_hash",
    ):
        assert column in _STAGE_A_SQL, f"persisted Stage A column renamed: {column}"


def test_persisted_stage_b_columns_are_pinned() -> None:
    """save_stage_b writes exactly the locked Stage B columns."""
    for column in (
        "stage_b_verdict",
        "stage_b_jd_summary",
        "stage_b_verdict_json",
        "stage_b_summary_json",
        "stage_b_fit_json",
        "stage_b_hooks_json",
        "stage_b_model",
        "stage_b_cost_usd",
        "stage_b_prompt_hash",
        "stage_b_resume_hash",
    ):
        assert column in _STAGE_B_SQL, f"persisted Stage B column renamed: {column}"


def test_top_evaluated_jobs_has_stable_tie_break() -> None:
    """Equal scores must use discovery recency and identity deterministically."""
    assert (
        "ORDER BY evaluations.stage_a_score DESC, "
        "jobs.discovered_at DESC, jobs.id DESC" in _TOP_EVALUATED_SQL
    )
