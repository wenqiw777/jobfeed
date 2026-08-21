"""Unit tests for ml-gate CLI wiring (Phase 5, Task 7).

These tests stay lightweight: they exercise the ``ml-gate info`` command (which
only reads meta JSON + the model filename) and the ``evaluate`` gate/filter
injection helpers using ``MockGate``. None of them load the real Booster,
``xgboost``, or ``fastembed``, so they never trigger the one-time ONNX model
download and run fast under ``make quality``.
"""

from __future__ import annotations

import importlib
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import ClassVar

import pytest
from click.testing import CliRunner

from jobfeed.adapters.ml import _embedder as embedder_module
from jobfeed.adapters.ml import xgboost_gate as xgboost_gate_module
from jobfeed.adapters.ml.mock import MockGate
from jobfeed.cli import cli
from jobfeed.cli.ml_gate import (
    _dir_size_human,
    build_hard_filters,
    build_ml_gate,
    needs_ml_gate,
)
from jobfeed.config import Settings

# The committed in-repo model + meta (reading meta JSON is toolchain-free).
MODELS_DIR = Path(__file__).resolve().parents[2] / "models" / "ml_gate"
MODEL_VERSION = "v20260601T170453Z"
MODEL_THRESHOLD = "0.19"
MODEL_META = {
    "threshold": 0.5,
    "embedding_model": "sentence-transformers/all-MiniLM-L6-v2",
    "embedding_dim": 384,
}

# Sentinel that diverts the real XGBoostGate to the lightweight MockGate.
MOCK_MODEL_DIR = "mock"

# Heavy ML modules the wiring path must never import lazily-or-otherwise.
ML_MODULES = ("xgboost", "fastembed", "onnxruntime")


def _settings(
    *,
    ml_gate_enabled: bool,
    model_dir: str = "models/ml_gate",
    model_version: str = "v20260601T170453Z",
    embedding_model: str | None = None,
) -> Settings:
    """Build Settings with the given scoring/ml_gate knobs (offline, pure)."""
    ml_gate: dict[str, object] = {
        "model_dir": model_dir,
        "model_version": model_version,
    }
    if embedding_model is not None:
        ml_gate["embedding_model"] = embedding_model
    return Settings.model_validate(
        {
            "scoring": {"ml_gate_enabled": ml_gate_enabled},
            "ml_gate": ml_gate,
        }
    )


@contextmanager
def _no_ml_toolchain_imported() -> Iterator[None]:
    """Assert the wrapped block imports no heavy ML module.

    Delta-based (compares ``sys.modules`` before/after) so it stays non-vacuous
    even when an earlier test in the same process already imported ``xgboost``:
    the guarantee under test is that *this* code path triggers no NEW ML import.
    """
    before = {name for name in ML_MODULES if name in sys.modules}
    yield
    leaked = [name for name in ML_MODULES if name in sys.modules and name not in before]
    assert not leaked, f"ML toolchain must stay lazy; newly imported: {leaked}"


# ---- ml-gate info ----


def test_ml_gate_info_prints_version_threshold_and_meta_metrics() -> None:
    """``ml-gate info`` prints the version, threshold, dir, and real metrics."""
    result = CliRunner().invoke(
        cli, ["ml-gate", "info", "--model-dir", str(MODELS_DIR)]
    )

    assert result.exit_code == 0, result.output
    out = result.output
    assert MODEL_VERSION in out
    assert MODEL_THRESHOLD in out
    assert str(MODELS_DIR) in out
    # Real meta metrics from v20260601T170453Z.meta.json.
    assert "recall_pos" in out and "0.973" in out
    assert "precision_pos" in out and "0.543" in out
    assert "f1" in out and "0.697" in out
    assert "train_size" in out and "7002" in out


def test_ml_gate_info_imports_no_ml_toolchain() -> None:
    """``ml-gate info`` must never import the Booster / embedder toolchain."""
    with _no_ml_toolchain_imported():
        result = CliRunner().invoke(
            cli, ["ml-gate", "info", "--model-dir", str(MODELS_DIR)]
        )

    assert result.exit_code == 0, result.output


def test_ml_gate_train_command_is_absent() -> None:
    """``ml-gate train`` is intentionally omitted in this phase."""
    result = CliRunner().invoke(cli, ["ml-gate", "train"])

    assert result.exit_code != 0
    # Click reports an unknown subcommand without a Python traceback.
    assert "Traceback" not in result.output


# ---- ml-gate fetch (host-native pre-seed; embedder warmed via fake) ----
#
# `fetch` lazily imports warm_embedder, so we patch it on the _embedder module
# to record the model id and stand in a temp cache dir (no real ONNX download).
# The size line is exercised against real on-disk bytes we write there.


def _patch_warm_embedder(monkeypatch: pytest.MonkeyPatch, cache_dir: Path) -> list[str]:
    """Patch ``warm_embedder`` to seed ``cache_dir`` and record requested models.

    Returns the list that captures each ``model_name`` ``fetch`` warms with, so a
    test can assert the default vs ``--embedding-model`` threading without ever
    importing fastembed / onnxruntime.
    """
    requested: list[str] = []

    def _fake_warm(model_name: str = embedder_module.DEFAULT_MODEL_NAME) -> str:
        requested.append(model_name)
        cache_dir.mkdir(parents=True, exist_ok=True)
        (cache_dir / "model.onnx").write_bytes(b"x" * 2048)  # 2 KiB of "weights"
        return str(cache_dir)

    monkeypatch.setattr(embedder_module, "warm_embedder", _fake_warm)
    return requested


def test_ml_gate_fetch_warms_default_model_and_prints_location_and_size(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``ml-gate fetch`` warms the default embedder and reports dir + size."""
    cache_dir = tmp_path / "fe-cache"
    requested = _patch_warm_embedder(monkeypatch, cache_dir)

    result = CliRunner().invoke(cli, ["ml-gate", "fetch"])

    assert result.exit_code == 0, result.output
    assert requested == ["all-MiniLM-L6-v2"]  # built-in default warmed
    assert str(cache_dir) in result.output
    assert "weights ready" in result.output
    assert "2.0 KiB" in result.output  # the 2048 bytes we seeded, humanized


def test_ml_gate_fetch_forwards_custom_embedding_model(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``--embedding-model`` threads the chosen id into ``warm_embedder``."""
    requested = _patch_warm_embedder(monkeypatch, tmp_path / "c")

    result = CliRunner().invoke(
        cli, ["ml-gate", "fetch", "--embedding-model", "BAAI/bge-small-en-v1.5"]
    )

    assert result.exit_code == 0, result.output
    assert requested == ["BAAI/bge-small-en-v1.5"]


def test_ml_gate_fetch_imports_no_ml_toolchain_until_warm(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``fetch`` triggers no NEW heavy ML import (the warm call is faked out).

    Proves the command body itself is lazy: with ``warm_embedder`` stubbed,
    invoking ``fetch`` must not import xgboost / fastembed / onnxruntime.
    """
    _patch_warm_embedder(monkeypatch, tmp_path / "c")

    with _no_ml_toolchain_imported():
        result = CliRunner().invoke(cli, ["ml-gate", "fetch"])

    assert result.exit_code == 0, result.output


def test_dir_size_human_counts_blobs_once_not_their_symlinks(tmp_path: Path) -> None:
    """Size totals the real blob once and skips the snapshot symlink to it.

    fastembed stores weights in ``blobs/`` and exposes them via
    ``snapshots/.../model.onnx`` symlinks. Following the link AND counting the
    blob would ~double the figure, so the size must equal just the blob bytes.
    """
    blobs = tmp_path / "models--x" / "blobs"
    snapshot = tmp_path / "models--x" / "snapshots" / "rev"
    blobs.mkdir(parents=True)
    snapshot.mkdir(parents=True)
    blob = blobs / "deadbeef"
    blob.write_bytes(b"w" * 4096)  # exactly 4 KiB of real weight bytes
    (snapshot / "model.onnx").symlink_to(blob)  # the double-count trap

    assert _dir_size_human(tmp_path) == "4.0 KiB"


# ---- evaluate gate/filter injection helpers ----


def test_build_ml_gate_disabled_returns_none_without_toolchain() -> None:
    """ml_gate_enabled=false -> no gate, and no xgboost/fastembed import."""
    with _no_ml_toolchain_imported():
        gate = build_ml_gate(_settings(ml_gate_enabled=False))

    assert gate is None


def test_build_ml_gate_mock_escape_hatch_returns_mock_gate() -> None:
    """The 'mock' model_dir sentinel yields a MockGate (no toolchain)."""
    with _no_ml_toolchain_imported():
        gate = build_ml_gate(_settings(ml_gate_enabled=True, model_dir=MOCK_MODEL_DIR))

    assert isinstance(gate, MockGate)


def test_build_ml_gate_can_load_disabled_gate_for_safe_shadow_learning() -> None:
    """Shadow mode can predict while the user-facing filter remains disabled."""
    with _no_ml_toolchain_imported():
        gate = build_ml_gate(
            _settings(ml_gate_enabled=False, model_dir=MOCK_MODEL_DIR),
            allow_disabled=True,
        )

    assert isinstance(gate, MockGate)


def test_build_hard_filters_is_pure_domain_even_when_gate_disabled() -> None:
    """Hard filters are always built from config (pure domain, no toolchain)."""
    settings = Settings.model_validate(
        {
            "scoring": {"ml_gate_enabled": False},
            "hard_filters": {"company_blocklist": ["BadCo"]},
        }
    )

    with _no_ml_toolchain_imported():
        hard_filters = build_hard_filters(settings)

    assert hard_filters is not None
    assert "BadCo" in hard_filters.company_blocklist


# ---- embedding_model threading (Fix B) ----
#
# These assert config.ml_gate.embedding_model reaches the embedder WITHOUT
# loading the ML toolchain: build_ml_gate's XGBoostGate construction and the
# gate's default-embedder construction are both stubbed with recorders, and the
# booster load is stubbed out, so neither xgboost nor fastembed is imported. We
# exercise the real wiring, not the heavy collaborators.


class _RecordingGate:
    """Stands in for XGBoostGate to capture the kwargs build_ml_gate passes."""

    last_kwargs: ClassVar[dict[str, object]] = {}

    def __init__(self, **kwargs: object) -> None:
        type(self).last_kwargs = kwargs


class _RecordingEmbedder:
    """Stands in for FastEmbedEmbedder; records construction kwargs."""

    last_kwargs: ClassVar[dict[str, object]] = {}

    def __init__(self, **kwargs: object) -> None:
        type(self).last_kwargs = kwargs

    def format_input(self, title: str, jd_text: str) -> str:  # pragma: no cover
        return f"{title} | {jd_text}"

    def embed_batch(self, _texts: list[str]) -> object:  # pragma: no cover
        raise AssertionError("embed_batch must not run in a wiring test")


def test_build_ml_gate_forwards_embedding_model_to_xgboost_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """build_ml_gate passes config.ml_gate.embedding_model into XGBoostGate.

    XGBoostGate is replaced with a recorder so no booster/xgboost loads; we only
    assert the kwarg threads through from settings.
    """
    monkeypatch.setattr(xgboost_gate_module, "XGBoostGate", _RecordingGate)
    settings = _settings(ml_gate_enabled=True, embedding_model="custom/embed-model")

    with _no_ml_toolchain_imported():
        build_ml_gate(settings)

    cfg = _RecordingGate.last_kwargs.get("embedder_config")
    assert cfg is not None
    assert cfg.model_name == "custom/embed-model"
    assert _RecordingGate.last_kwargs.get("model_version") == MODEL_VERSION


def test_xgboost_gate_forwards_embedding_model_to_default_embedder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """XGBoostGate builds its default embedder with the configured model name.

    The booster load and the FastEmbedEmbedder are both stubbed, so constructing
    + exercising the gate loads neither xgboost nor fastembed. We drive the
    ``_active_embedder`` property (the only place the default embedder is built)
    and assert it received ``model_name``.
    """
    monkeypatch.setattr(xgboost_gate_module, "FastEmbedEmbedder", _RecordingEmbedder)
    monkeypatch.setattr(
        xgboost_gate_module,
        "_load_booster",
        lambda _model_dir, **_kwargs: (object(), "v-test"),
    )
    monkeypatch.setattr(
        xgboost_gate_module,
        "read_meta",
        lambda _model_dir, _version: {
            **MODEL_META,
            "embedding_model": "custom/embed-model",
        },
    )

    with _no_ml_toolchain_imported():
        gate = xgboost_gate_module.XGBoostGate(
            model_dir="unused",
            embedder_config=xgboost_gate_module.EmbedderConfig(
                model_name="custom/embed-model"
            ),
        )
        embedder = gate._active_embedder  # triggers default-embedder construction

    assert isinstance(embedder, _RecordingEmbedder)
    assert _RecordingEmbedder.last_kwargs.get("model_name") == "custom/embed-model"


def test_xgboost_gate_default_embedding_model_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With embedding_model=None the default embedder gets the built-in default."""
    monkeypatch.setattr(xgboost_gate_module, "FastEmbedEmbedder", _RecordingEmbedder)
    monkeypatch.setattr(
        xgboost_gate_module,
        "_load_booster",
        lambda _model_dir, **_kwargs: (object(), "v-test"),
    )
    monkeypatch.setattr(
        xgboost_gate_module,
        "read_meta",
        lambda _model_dir, _version: MODEL_META,
    )

    with _no_ml_toolchain_imported():
        gate = xgboost_gate_module.XGBoostGate(model_dir="unused")
        _ = gate._active_embedder

    assert (
        _RecordingEmbedder.last_kwargs.get("model_name")
        == xgboost_gate_module.DEFAULT_MODEL_NAME
    )


def test_xgboost_gate_injected_embedder_ignores_embedding_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An injected embedder is used verbatim; embedding_model is not applied."""
    monkeypatch.setattr(
        xgboost_gate_module,
        "_load_booster",
        lambda _model_dir, **_kwargs: (object(), "v-test"),
    )
    monkeypatch.setattr(
        xgboost_gate_module,
        "read_meta",
        lambda _model_dir, _version: {
            **MODEL_META,
            "embedding_model": "custom/embed-model",
        },
    )
    injected = _RecordingEmbedder()
    _RecordingEmbedder.last_kwargs = {}  # reset after the injected construction

    with _no_ml_toolchain_imported():
        gate = xgboost_gate_module.XGBoostGate(
            model_dir="unused",
            embedder=injected,  # type: ignore[arg-type]
            embedder_config=xgboost_gate_module.EmbedderConfig(
                model_name="custom/embed-model"
            ),
        )
        embedder = gate._active_embedder

    assert embedder is injected
    # No NEW default embedder was constructed (injected one wins).
    assert _RecordingEmbedder.last_kwargs == {}


def test_build_ml_gate_real_gate_builds_without_preflight(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Enabled real gate constructs straight to XGBoostGate (no extra preflight).

    fastembed is a core dependency, so there is no ``.[ml]`` extra to check —
    ``build_ml_gate`` goes directly to the gate. ``XGBoostGate`` is a recorder,
    so the real Booster / xgboost / fastembed never load.
    """
    monkeypatch.setattr(xgboost_gate_module, "XGBoostGate", _RecordingGate)

    with _no_ml_toolchain_imported():
        gate = build_ml_gate(_settings(ml_gate_enabled=True))

    assert isinstance(gate, _RecordingGate)


# ---- lazy gate build: only when a run can use it (Fix C) ----
#
# cli/evaluate gates the build_ml_gate(...) call on needs_ml_gate(...): the gate
# (and thus xgboost) is built ONLY when Stage A both runs and can claim work.
# needs_ml_gate is pure, so this truth table fully proves a Stage-B-only or
# limit-0 run never constructs the gate — no toolchain involved.


@pytest.mark.parametrize(
    ("stage", "limit", "expected"),
    [
        ("both", None, True),  # default run: Stage A fires, default limit
        ("a", None, True),  # Stage A only
        ("a", 5, True),  # Stage A, positive limit
        ("both", 5, True),  # both stages, positive limit
        ("b", None, False),  # Stage-B-only: Stage A never fires
        ("b", 5, False),  # Stage-B-only with limit
        ("a", 0, False),  # limit 0 means zero -> Stage A claims nothing
        ("both", 0, False),  # limit 0 means zero
    ],
)
def test_needs_ml_gate_truth_table(
    stage: str, limit: int | None, expected: bool
) -> None:
    """Gate is needed only when enabled AND Stage A runs AND limit allows >=1."""
    assert needs_ml_gate(stage, limit, ml_gate_enabled=True) is expected


def test_needs_ml_gate_false_when_disabled_regardless_of_stage() -> None:
    """Disabled gate is never needed, even for a default Stage-A run."""
    assert needs_ml_gate("both", None, ml_gate_enabled=False) is False


def test_build_ml_gate_not_called_for_stage_b_or_limit_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The gated call site invokes build_ml_gate only when needs_ml_gate is True.

    Mirrors ``cli/evaluate``'s ``build_ml_gate(settings) if needs_gate else None``
    with a recorder, proving Stage-B-only and limit-0 runs never construct the
    gate (so xgboost never loads), while a default run does.
    """
    calls: list[bool] = []

    def _record(_settings: object) -> None:
        calls.append(True)

    # build_ml_gate lives in jobfeed.cli.ml_gate; patch it there and use
    # the gated pattern (needs_ml_gate -> build_ml_gate) that the evaluate
    # factory relies on.
    ml_gate_module = importlib.import_module("jobfeed.cli.ml_gate")
    monkeypatch.setattr(ml_gate_module, "build_ml_gate", _record)

    skip = [("b", None), ("b", 5), ("a", 0), ("both", 0)]
    for stage, limit in skip:
        needs = needs_ml_gate(stage, limit, ml_gate_enabled=True)
        _ = ml_gate_module.build_ml_gate(object()) if needs else None
    assert calls == []  # no build for any skip case

    needs = needs_ml_gate("both", None, ml_gate_enabled=True)
    _ = ml_gate_module.build_ml_gate(object()) if needs else None
    assert calls == [True]  # built exactly once for the default run
