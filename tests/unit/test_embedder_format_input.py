"""Pure tests for ``FastEmbedEmbedder`` (format_input + lazy model + aliasing).

Lightweight: importing ``_embedder`` only pulls numpy + stdlib at module scope
(``fastembed`` is imported lazily, on the first ``embed_batch``), and these tests
never embed — they exercise the slice math, the model-name aliasing, and the
stored ``model_name`` without loading the ONNX model. This runs under
``make quality`` without triggering any model download.

Focus: (1) the legacy JD-truncation slice end ``max_chars - len(title) - 3``
must be clamped at 0, so a title longer than ``max_chars`` truncates the JD from
the START (empty JD half) rather than an unintended from-the-end negative slice;
(2) the legacy ``all-MiniLM-L6-v2`` short name is mapped to fastembed's full
Hugging Face id at construction WITHOUT importing fastembed (the model loads only
on first ``embed_batch``), and any other id is passed through verbatim.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path
from typing import Any, ClassVar

import pytest

from jobfeed.adapters.ml import _embedder as embedder_module
from jobfeed.adapters.ml._embedder import (
    DEFAULT_MODEL_NAME,
    FASTEMBED_MINILM_ID,
    ML_CACHE_DIR_ENV,
    FastEmbedEmbedder,
)

_LAZY_MODULES = ("fastembed", "onnxruntime")


def _embedder(max_chars: int) -> FastEmbedEmbedder:
    """Build an embedder WITHOUT loading the model (no fastembed import).

    Bypasses ``__init__`` (which would lazily load fastembed on first embed) and
    sets only the attribute ``format_input`` reads.
    """
    obj = FastEmbedEmbedder.__new__(FastEmbedEmbedder)
    obj._max_chars = max_chars
    return obj


def test_format_input_normal_title_truncates_jd_from_start() -> None:
    """A short title leaves a positive budget; the JD is sliced from the start."""
    embedder = _embedder(2000)
    title = "Engineer"
    jd_text = "x" * 5000
    result = embedder.format_input(title, jd_text)

    expected_jd_len = 2000 - len(title) - 3
    assert result == f"{title} | {'x' * expected_jd_len}"
    # The kept JD is the PREFIX of the original, not a tail slice.
    assert result.endswith("x" * expected_jd_len)


def test_format_input_long_title_yields_empty_jd_not_tail_slice() -> None:
    """A title longer than max_chars clamps the slice end to 0 (empty JD half).

    Without the clamp, ``max_chars - len(title) - 3`` is negative and
    ``jd_text[:negative]`` would keep the JD MINUS its tail — silently leaking
    JD content and violating the truncate-from-start contract. We assert the JD
    half is empty instead.
    """
    max_chars = 2000
    embedder = _embedder(max_chars)
    title = "T" * 2100  # len(title) > max_chars -> negative pre-clamp end
    jd_text = "abcdefghijklmnopqrstuvwxyz" * 200  # distinctive, > a few chars

    result = embedder.format_input(title, jd_text)

    # JD half is empty: nothing after the "| " separator.
    assert result == f"{title} | "
    # Guard against the from-the-end regression: the original (long) JD body must
    # NOT appear in the output.
    jd_tail = jd_text[-50:]
    assert jd_tail not in result


def test_format_input_title_exactly_consuming_budget_is_empty() -> None:
    """A title at the boundary (max_chars - 3) leaves a zero-length JD slice."""
    max_chars = 2000
    embedder = _embedder(max_chars)
    title = "Q" * (max_chars - 3)  # end == 0 exactly
    result = embedder.format_input(title, "payload-should-be-dropped")
    assert result == f"{title} | "


def test_construction_maps_legacy_default_to_fastembed_id_without_loading() -> None:
    """The default short name is mapped to fastembed's full id; no fastembed import.

    Constructing the embedder must be cheap + lazy (the model loads only on first
    ``embed_batch``). We assert the legacy ``all-MiniLM-L6-v2`` default resolves
    to the full Hugging Face id and that NO fastembed / onnxruntime import was
    triggered by construction.
    """
    before = {name for name in _LAZY_MODULES if name in sys.modules}

    embedder = FastEmbedEmbedder()

    assert embedder._model_name == FASTEMBED_MINILM_ID
    assert embedder._model is None  # not materialized at construction
    leaked = [
        name for name in _LAZY_MODULES if name in sys.modules and name not in before
    ]
    assert not leaked, f"construction must stay lazy; newly imported: {leaked}"


def test_construction_passes_through_custom_model_name() -> None:
    """A non-legacy ``model_name`` is stored verbatim (no aliasing, no load)."""
    before = {name for name in _LAZY_MODULES if name in sys.modules}
    model_name = "BAAI/bge-small-en-v1.5"

    embedder = FastEmbedEmbedder(model_name=model_name)

    assert embedder._model_name == model_name
    assert embedder._model is None
    leaked = [
        name for name in _LAZY_MODULES if name in sys.modules and name not in before
    ]
    assert not leaked, f"construction must stay lazy; newly imported: {leaked}"


def test_default_model_name_constant_is_legacy_short_name() -> None:
    """The exported default stays the legacy short name (config back-compat)."""
    assert DEFAULT_MODEL_NAME == "all-MiniLM-L6-v2"


class _SpyTextEmbedding:
    """Records the model_name + cache_dir a TextEmbedding is constructed with."""

    calls: ClassVar[list[dict[str, Any]]] = []

    def __init__(self, model_name: str, *, cache_dir: str | None = None) -> None:
        type(self).calls.append({"model_name": model_name, "cache_dir": cache_dir})


def _install_fake_fastembed(monkeypatch: pytest.MonkeyPatch, module: type[Any]) -> None:
    """Inject a fake ``fastembed`` module so ``_load_model``'s lazy import is a spy.

    ``_load_model`` does ``from fastembed import TextEmbedding`` at call time, so a
    stub module in ``sys.modules`` is resolved instead of the real (ONNX) package —
    keeping the test toolchain-free.
    """
    fake = types.ModuleType("fastembed")
    fake.TextEmbedding = module  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "fastembed", fake)


def test_load_model_passes_resolved_cache_dir_from_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``_load_model`` forwards the env-configured cache_dir to TextEmbedding.

    With ``$JOBFEED_ML_CACHE_DIR`` set, the resolved (created) directory is passed
    straight through to ``fastembed.TextEmbedding(cache_dir=...)`` — the seam that
    pins the ONNX weights onto a persistent Docker volume across ``--rm`` runs.
    """
    _SpyTextEmbedding.calls = []
    cache_dir = tmp_path / "fe-cache"
    monkeypatch.setenv(ML_CACHE_DIR_ENV, str(cache_dir))
    _install_fake_fastembed(monkeypatch, _SpyTextEmbedding)

    embedder_module._load_model("some/model")

    assert _SpyTextEmbedding.calls == [
        {"model_name": "some/model", "cache_dir": str(cache_dir)}
    ]
    # The resolver creates the directory so the first download can write into it.
    assert cache_dir.is_dir()


def test_load_model_defaults_cache_dir_under_user_cache_jobfeed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Without the env override, cache_dir defaults under ~/.cache/jobfeed.

    The host-native default keeps weights persistent with zero config and never
    touches ~/.jobfeed (read-only user data).
    """
    _SpyTextEmbedding.calls = []
    monkeypatch.delenv(ML_CACHE_DIR_ENV, raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda _cls: tmp_path))
    _install_fake_fastembed(monkeypatch, _SpyTextEmbedding)

    embedder_module._load_model("some/model")

    expected = tmp_path / ".cache" / "jobfeed" / "fastembed"
    assert _SpyTextEmbedding.calls == [
        {"model_name": "some/model", "cache_dir": str(expected)}
    ]
    assert expected.is_dir()
