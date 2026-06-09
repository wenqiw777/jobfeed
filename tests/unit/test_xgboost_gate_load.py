"""Load + predict tests for the XGBoostGate adapter against a TINY fixture model.

Runs under ``make quality``: uses xgboost (installed in dev/CI) but never
fastembed or the real committed model — the ``xgboost`` import is
lazy in the adapter, and a FAKE counting embedder supplies fixed 384-vectors so
every score is deterministic. The REAL model + REAL embedder are exercised
separately in a ``@pytest.mark.mlmodel`` test (Task 9).

The tiny fixture (``tests/fixtures/ml_gate_tiny_model.json``) is a 4-round
``binary:logistic`` booster with ``num_feature=450`` whose label loosely depends
on structured cols 17 (a domain flag) and 65 (is_swe); with a zero-fill
embedding it yields a clean spread of pass/fail scores around threshold 0.5,
which these tests pin.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import numpy as np
import pytest

from jobfeed.adapters.ml.xgboost_gate import XGBoostGate
from jobfeed.ports.ml_gate import GateInput, MLGate

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
TINY_MODEL = FIXTURES / "ml_gate_tiny_model.json"
TINY_META = FIXTURES / "ml_gate_tiny_model.meta.json"
WRONG_OBJ_MODEL = FIXTURES / "ml_gate_wrong_objective.json"
WRONG_OBJ_META = FIXTURES / "ml_gate_wrong_objective.meta.json"

META_THRESHOLD = 0.5
EMBED_DIM = 384

# Clean SWE postings that clear every hard-fail rule (is_swe, no clearance,
# yoe<2). With a zero-fill embedding the tiny model scores "react" >= 0.5 (pass)
# and "plain" ~0.42 (< 0.5 meta threshold, but >= a 0.3 override).
PASS_TITLE = "Frontend Engineer"
PASS_JD = "Build UIs with React and TypeScript. Write clean code."
FAIL_TITLE = "Software Engineer"
FAIL_JD = "Entry-level role. Write code in Python. Join us building services."

# A hard-fail posting: non-SWE role -> fails before any embedding.
HARDFAIL_TITLE = "Sales Manager"
HARDFAIL_JD = "Sell products to enterprise clients. Hit quota."
HARDFAIL_REASON = "not software engineering role"

OVERRIDE_BELOW_PLAIN = 0.3  # plain scores ~0.42 -> passes at this override


class CountingFakeEmbedder:
    """Fake embedder: returns a fixed fill vector and records embedded texts."""

    def __init__(self, fill: float = 0.0) -> None:
        self._fill = fill
        self.embedded_texts: list[str] = []
        self.batch_calls = 0

    def format_input(self, title: str, jd_text: str) -> str:
        return f"{title} | {jd_text}"

    def embed_batch(self, texts: list[str]) -> np.ndarray:
        self.batch_calls += 1
        self.embedded_texts.extend(texts)
        return np.full((len(texts), EMBED_DIM), self._fill, dtype=np.float32)


def _model_dir(tmp_path: Path, *, model: Path, meta: Path | None) -> Path:
    """Stage a model (+optional meta) as a ``v*.json`` pair in ``tmp_path``."""
    shutil.copy(model, tmp_path / "v20260101T000000Z.json")
    if meta is not None:
        shutil.copy(meta, tmp_path / "v20260101T000000Z.meta.json")
    return tmp_path


def _gate(
    tmp_path: Path, embedder: CountingFakeEmbedder, **kwargs: object
) -> XGBoostGate:
    model_dir = _model_dir(tmp_path, model=TINY_MODEL, meta=TINY_META)
    return XGBoostGate(model_dir=model_dir, embedder=embedder, **kwargs)  # type: ignore[arg-type]


def test_gate_satisfies_ml_gate_protocol(tmp_path: Path) -> None:
    """XGBoostGate structurally implements the MLGate port."""
    assert isinstance(_gate(tmp_path, CountingFakeEmbedder()), MLGate)


def test_loads_latest_model_and_meta_threshold(tmp_path: Path) -> None:
    """The gate picks up the model stem as version and the meta threshold."""
    gate = _gate(tmp_path, CountingFakeEmbedder())
    assert gate._version == "v20260101T000000Z"
    assert gate._threshold == META_THRESHOLD


def test_loads_latest_when_multiple_versions(tmp_path: Path) -> None:
    """With several v*.json present, the lexicographically-latest stem wins."""
    shutil.copy(TINY_MODEL, tmp_path / "v20250101T000000Z.json")
    shutil.copy(TINY_MODEL, tmp_path / "v20270101T000000Z.json")
    shutil.copy(TINY_META, tmp_path / "v20270101T000000Z.meta.json")
    gate = XGBoostGate(model_dir=tmp_path, embedder=CountingFakeEmbedder())
    assert gate._version == "v20270101T000000Z"


def test_missing_model_raises_file_not_found(tmp_path: Path) -> None:
    """An empty model_dir raises naturally (no ModelNotFoundError defined)."""
    with pytest.raises(FileNotFoundError):
        XGBoostGate(model_dir=tmp_path, embedder=CountingFakeEmbedder())


def test_wrong_objective_fails_fast(tmp_path: Path) -> None:
    """A non-binary:logistic booster raises a clear ValueError at load."""
    model_dir = _model_dir(tmp_path, model=WRONG_OBJ_MODEL, meta=WRONG_OBJ_META)
    with pytest.raises(ValueError, match="objective"):
        XGBoostGate(model_dir=model_dir, embedder=CountingFakeEmbedder())


def test_missing_meta_fails_fast(tmp_path: Path) -> None:
    """A model with no meta sidecar cannot choose hidden threshold/contracts."""
    model_dir = _model_dir(tmp_path, model=TINY_MODEL, meta=None)
    with pytest.raises(FileNotFoundError, match="meta"):
        XGBoostGate(model_dir=model_dir, embedder=CountingFakeEmbedder())


def test_configured_embedding_model_must_match_meta(tmp_path: Path) -> None:
    """A mismatched configured embedder id is rejected at gate construction."""
    model_dir = _model_dir(tmp_path, model=TINY_MODEL, meta=TINY_META)
    with pytest.raises(ValueError, match="embedding_model"):
        XGBoostGate(
            model_dir=model_dir,
            embedder=None,
            embedding_model="BAAI/bge-small-en-v1.5",
        )


async def test_empty_batch_returns_empty(tmp_path: Path) -> None:
    """An empty input list short-circuits to an empty result list."""
    gate = _gate(tmp_path, CountingFakeEmbedder())
    assert await gate.predict_batch([]) == []


async def test_model_pass_sets_version_and_no_reason(tmp_path: Path) -> None:
    """A clean posting scoring >= threshold passes with fail_reason=None."""
    gate = _gate(tmp_path, CountingFakeEmbedder(fill=0.0))
    [res] = await gate.predict_batch(
        [GateInput(job_id="p", title=PASS_TITLE, jd_text=PASS_JD)]
    )
    assert res.result == "pass"
    assert res.fail_reason is None
    assert res.score >= META_THRESHOLD
    assert res.version == "v20260101T000000Z"


async def test_model_fail_has_no_reason(tmp_path: Path) -> None:
    """A model-driven fail (score < threshold) carries fail_reason=None."""
    gate = _gate(tmp_path, CountingFakeEmbedder(fill=0.0))
    [res] = await gate.predict_batch(
        [GateInput(job_id="f", title=FAIL_TITLE, jd_text=FAIL_JD)]
    )
    assert res.result == "fail"
    assert res.fail_reason is None
    assert 0.0 < res.score < META_THRESHOLD


async def test_threshold_override_beats_meta(tmp_path: Path) -> None:
    """An override below the model fail's score flips fail -> pass."""
    embedder = CountingFakeEmbedder(fill=0.0)
    gate = _gate(tmp_path, embedder, threshold_override=OVERRIDE_BELOW_PLAIN)
    assert gate._threshold == OVERRIDE_BELOW_PLAIN
    [res] = await gate.predict_batch(
        [GateInput(job_id="f", title=FAIL_TITLE, jd_text=FAIL_JD)]
    )
    assert res.result == "pass"
    assert res.fail_reason is None


async def test_hard_fail_short_circuits_without_embedding(tmp_path: Path) -> None:
    """Hard-failed rows skip embedding entirely (fake embedder never sees them)."""
    embedder = CountingFakeEmbedder(fill=0.0)
    gate = _gate(tmp_path, embedder)
    [res] = await gate.predict_batch(
        [GateInput(job_id="h", title=HARDFAIL_TITLE, jd_text=HARDFAIL_JD)]
    )
    assert res.result == "fail"
    assert res.fail_reason == HARDFAIL_REASON
    assert res.score == 0.0
    # No survivors -> embed_batch is never called for the hard-failed input.
    assert embedder.batch_calls == 0
    assert embedder.embedded_texts == []


async def test_batch_realigns_with_interleaved_hard_fail(tmp_path: Path) -> None:
    """Results re-align to input order with a hard-failed row in the middle."""
    embedder = CountingFakeEmbedder(fill=0.0)
    gate = _gate(tmp_path, embedder)
    jobs = [
        GateInput(job_id="0-pass", title=PASS_TITLE, jd_text=PASS_JD),
        GateInput(job_id="1-hard", title=HARDFAIL_TITLE, jd_text=HARDFAIL_JD),
        GateInput(job_id="2-fail", title=FAIL_TITLE, jd_text=FAIL_JD),
    ]
    results = await gate.predict_batch(jobs)

    assert len(results) == 3
    # Row 0: model pass.
    assert results[0].result == "pass"
    assert results[0].fail_reason is None
    # Row 1: hard fail, never embedded.
    assert results[1].result == "fail"
    assert results[1].fail_reason == HARDFAIL_REASON
    assert results[1].score == 0.0
    # Row 2: model fail.
    assert results[2].result == "fail"
    assert results[2].fail_reason is None
    assert 0.0 < results[2].score < META_THRESHOLD

    # Only the two survivors were embedded, in their original relative order.
    assert embedder.batch_calls == 1
    assert len(embedder.embedded_texts) == 2
    assert embedder.embedded_texts[0].startswith(PASS_TITLE)
    assert embedder.embedded_texts[1].startswith(FAIL_TITLE)


async def test_feature_columns_int_to_bool_coercion(tmp_path: Path) -> None:
    """clearance_required / school_restricted surface as bool, not int 0/1."""
    embedder = CountingFakeEmbedder(fill=0.0)
    gate = _gate(tmp_path, embedder)
    [res] = await gate.predict_batch(
        [GateInput(job_id="p", title=PASS_TITLE, jd_text=PASS_JD)]
    )
    assert isinstance(res.clearance_required, bool)
    assert isinstance(res.school_restricted, bool)
    assert isinstance(res.is_swe_role, bool)
    # Feature columns are populated from the real extractor.
    assert res.role_type == "fte"
    assert "typescript" in (res.tech_required or [])
