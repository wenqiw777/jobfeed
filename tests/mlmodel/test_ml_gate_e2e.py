"""End-to-end @mlmodel gate test: real committed model + real ONNX embedder.

This is the Phase 5 (Rev3) replacement for the old legacy bit-for-bit parity
suite. Instead of pinning embedder/runtime versions, a batch size, a
cross-machine score band, or a perturbation-stable near-threshold fixture, it
exercises the WHOLE gate chain (extract -> hard-fail -> embed -> vectorize ->
predict) on the real artifacts and asserts only *unambiguous* pass/fail
decisions:

* the committed in-repo XGBoost model under ``models/ml_gate/``
  (``v20260601T170453Z.json`` + ``.meta.json``, threshold 0.19), and
* the real ``FastEmbedEmbedder`` (``all-MiniLM-L6-v2`` over onnxruntime, ONNX
  weights downloaded from Hugging Face on first run).

Run it with ``pytest -m mlmodel``. It is excluded from the default ``addopts``
(and therefore from ``make quality``) because it triggers a one-time model
download from Hugging Face.

The expected decisions below were obtained by RUNNING this exact gate over
these jobs. Clear non-SDE rows short-circuit to ``score == 0.0`` with a rule
reason. Experience and clearance postings receive normal model scores; the
evaluation service makes those scores nonblocking for admission.
"""

from __future__ import annotations

import pytest

from jobfeed.adapters.ml._embedder import FastEmbedEmbedder
from jobfeed.adapters.ml.xgboost_gate import XGBoostGate
from jobfeed.ports.ml_gate import GateInput

pytestmark = pytest.mark.mlmodel

MODEL_DIR = "models/ml_gate"

# A clear entry-level SWE job description reused by the simplest pass cases:
# explicit languages + concrete software-engineering signals (write code, PRs,
# REST APIs, Docker/Kubernetes, git, CI/CD) so ``is_swe_role`` is unambiguous.
_SWE_JD = (
    "We are hiring a Software Engineer to build backend services in Python "
    "and Java. You will write code, review pull requests, work with SQL and "
    "PostgreSQL, deploy with Docker and Kubernetes, and build REST APIs. "
    "Familiarity with git, microservices, and CI/CD is a plus. Agile team."
)


# Each case is a clear non-SDE role that must short-circuit before embedding.
_HARD_FAIL_CASES: list[tuple[GateInput, str]] = [
    # Clearly non-SWE role (marketing) => not-software-engineering hard-fail.
    (
        GateInput(
            job_id="hardfail-marketing",
            title="Marketing Manager",
            jd_text=(
                "We are seeking a Marketing Manager to lead campaigns, manage "
                "brand strategy, run social media, and coordinate events. "
                "Strong communication and content writing skills required."
            ),
        ),
        "not software engineering role",
    ),
    # Clearly non-SWE role (nurse) => not-software-engineering hard-fail.
    (
        GateInput(
            job_id="hardfail-nurse",
            title="Registered Nurse",
            jd_text=(
                "Registered Nurse needed for our hospital. Provide patient "
                "care, administer medication, and support the clinical team. "
                "BSN and active RN license required."
            ),
        ),
        "not software engineering role",
    ),
]

_NONBLOCKING_POLICY_CASES: list[GateInput] = [
    GateInput(
        job_id="policy-yoe",
        title="Software Engineer",
        jd_text=(
            "We need a Software Engineer with 5+ years of experience building "
            "backend systems in Python, Java, SQL, Docker, Kubernetes, REST "
            "APIs, microservices, git and CI/CD."
        ),
    ),
    GateInput(
        job_id="policy-clearance",
        title="Backend Software Engineer",
        jd_text=(
            "Must hold an active TS/SCI security clearance. You will write code "
            "in Python and Java, build REST APIs, use Docker, Kubernetes, SQL, "
            "git, microservices and CI/CD."
        ),
    ),
]

_MODEL_PASS_CASES: list[GateInput] = [
    # Entry-level intern SWE.
    GateInput(
        job_id="pass-intern",
        title="Software Engineer Intern",
        jd_text=_SWE_JD,
    ),
    # New-grad SWE.
    GateInput(
        job_id="pass-new-grad",
        title="New Grad Software Engineer",
        jd_text=_SWE_JD,
    ),
    # Multi-tech backend role (no YoE / clearance triggers).
    GateInput(
        job_id="pass-backend",
        title="Backend Software Engineer",
        jd_text=(
            "Join our platform team as a Backend Software Engineer. Build "
            "distributed services in Go, Java and Python. Work with "
            "PostgreSQL, Redis, Kafka, gRPC, Docker, Kubernetes, AWS and "
            "Terraform. You'll write code, do code reviews, build REST and "
            "GraphQL APIs, and own CI/CD. Entry-level and new grads welcome."
        ),
    ),
    # Junior full-stack role across several languages/frameworks.
    GateInput(
        job_id="pass-fullstack",
        title="Junior Full Stack Engineer",
        jd_text=(
            "Junior Full Stack Engineer. Build web apps with React, "
            "TypeScript, Node.js on the frontend and Python/Django with "
            "PostgreSQL on the backend. Write code, review PRs, deploy with "
            "Docker. New grads ok."
        ),
    ),
]


@pytest.fixture(scope="module")
def gate() -> XGBoostGate:
    """Real gate: committed in-repo model + real ONNX ``all-MiniLM-L6-v2`` embedder.

    The embedder downloads the ONNX weights from Hugging Face on first run; the
    model and its 0.19 threshold come from the committed ``models/ml_gate/``
    artifacts.
    """
    return XGBoostGate(model_dir=MODEL_DIR, embedder=FastEmbedEmbedder())


@pytest.mark.asyncio
async def test_ml_gate_e2e_decisions(gate: XGBoostGate) -> None:
    """Run the full gate over clear blocks and nonblocking SDE metadata.

    Validates the whole chain end-to-end: every deterministic hard-fail returns
    its exact rule reason with ``score == 0.0``; YoE/clearance roles are model
    scored rather than short-circuited; clear SWE roles pass the model.
    """
    hard_fail_jobs = [job for job, _ in _HARD_FAIL_CASES]
    expected_reasons = {job.job_id: reason for job, reason in _HARD_FAIL_CASES}
    policy_ids = {job.job_id for job in _NONBLOCKING_POLICY_CASES}
    pass_ids = {job.job_id for job in _MODEL_PASS_CASES}

    # One mixed batch exercises the hard-fail short-circuit + model survivors
    # together and proves results stay aligned to input order.
    jobs = hard_fail_jobs + _NONBLOCKING_POLICY_CASES + _MODEL_PASS_CASES
    results = await gate.predict_batch(jobs)

    assert len(results) == len(jobs)
    by_id = {job.job_id: result for job, result in zip(jobs, results, strict=True)}

    for job_id, expected_reason in expected_reasons.items():
        result = by_id[job_id]
        assert result.result == "fail", f"{job_id} should hard-fail"
        assert result.fail_reason == expected_reason, job_id
        assert result.score == pytest.approx(0.0), job_id
        assert result.version == "v20260601T170453Z"

    for job_id in policy_ids:
        result = by_id[job_id]
        assert result.fail_reason is None, job_id
        assert result.is_swe_role is True, job_id
        assert 0.0 <= result.score <= 1.0, job_id
        assert result.version == "v20260601T170453Z"

    for job_id in pass_ids:
        result = by_id[job_id]
        assert result.result == "pass", f"{job_id} should pass the model"
        assert result.fail_reason is None, job_id
        assert 0.0 <= result.score <= 1.0, job_id
        assert result.is_swe_role is True, job_id
        assert result.version == "v20260601T170453Z"
