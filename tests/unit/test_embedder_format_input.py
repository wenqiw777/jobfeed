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
    warm_embedder,
    weights_present,
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


# ---- weights-present detection + one-time-download log (FIX 3) ----
#
# The eval path must announce a cold-cache embedder download ONCE (with a
# pre-seed hint) instead of silently stalling mid-evaluation, but stay silent
# when the weights are already cached — exactly the Docker-baked case. These
# tests drive _load_model with the fake fastembed so nothing downloads.


def _seed_onnx_weight(cache_dir: Path) -> None:
    """Create a fastembed-shaped ``model.onnx`` so ``weights_present`` is True."""
    snapshot = cache_dir / "models--qdrant--all-MiniLM-L6-v2-onnx" / "snapshots" / "rev"
    snapshot.mkdir(parents=True)
    (snapshot / "model.onnx").write_bytes(b"onnx")


def test_weights_present_false_for_missing_or_empty_dir(tmp_path: Path) -> None:
    """No cache dir and an empty cache dir both report weights absent."""
    assert weights_present(tmp_path / "does-not-exist") is False
    empty = tmp_path / "empty"
    empty.mkdir()
    assert weights_present(empty) is False


def test_weights_present_true_when_model_onnx_exists_anywhere(tmp_path: Path) -> None:
    """A nested ``model.onnx`` (fastembed snapshot layout) means present."""
    _seed_onnx_weight(tmp_path)
    assert weights_present(tmp_path) is True


def test_load_model_warns_once_on_cold_cache(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A cache MISS logs ``embedder_weights_downloading`` before the download."""
    _SpyTextEmbedding.calls = []
    cache_dir = tmp_path / "cold"
    monkeypatch.setenv(ML_CACHE_DIR_ENV, str(cache_dir))
    _install_fake_fastembed(monkeypatch, _SpyTextEmbedding)
    warned: list[dict[str, Any]] = []
    monkeypatch.setattr(
        embedder_module,
        "_warn_one_time_download",
        lambda model, cache: warned.append({"model": model, "cache_dir": cache}),
    )

    embedder_module._load_model("some/model")

    assert warned == [{"model": "some/model", "cache_dir": str(cache_dir)}]


def test_load_model_silent_when_weights_already_cached(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A warm cache (baked Docker image) emits no download warning."""
    _SpyTextEmbedding.calls = []
    cache_dir = tmp_path / "warm"
    cache_dir.mkdir()
    _seed_onnx_weight(cache_dir)
    monkeypatch.setenv(ML_CACHE_DIR_ENV, str(cache_dir))
    _install_fake_fastembed(monkeypatch, _SpyTextEmbedding)
    warned: list[object] = []
    monkeypatch.setattr(
        embedder_module,
        "_warn_one_time_download",
        lambda *_a: warned.append(object()),
    )

    embedder_module._load_model("some/model")

    assert warned == []  # weights present -> no surprise-download log


def test_warn_one_time_download_emits_structlog_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The warning helper emits a structlog event carrying the model + hint."""
    events: list[tuple[str, dict[str, Any]]] = []

    class _SpyLog:
        def warning(self, event: str, **kwargs: Any) -> None:
            events.append((event, kwargs))

    monkeypatch.setattr(
        embedder_module.structlog, "get_logger", lambda *_a, **_k: _SpyLog()
    )

    embedder_module._warn_one_time_download("some/model", "/tmp/cache")

    assert len(events) == 1
    event, kwargs = events[0]
    assert event == "embedder_weights_downloading"
    assert kwargs["model"] == "some/model"
    assert kwargs["cache_dir"] == "/tmp/cache"
    assert "ml-gate fetch" in kwargs["hint"]


def test_warm_embedder_loads_via_embed_batch_and_returns_cache_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``warm_embedder`` runs one embed (materializing the model) + returns the dir.

    Drives the real ``FastEmbedEmbedder.embed_batch`` through the fake fastembed
    so it loads + embeds without any download; asserts it returns the resolved
    cache dir (what ``fetch`` and the Docker bake print / write to).
    """
    cache_dir = tmp_path / "warm-embed"
    monkeypatch.setenv(ML_CACHE_DIR_ENV, str(cache_dir))
    embedded: list[list[str]] = []

    class _EmbeddingModel:
        def __init__(self, _name: str, *, cache_dir: str | None = None) -> None:
            self._dir = cache_dir

        def embed(self, texts: list[str]) -> list[Any]:
            embedded.append(list(texts))
            import numpy as np  # noqa: PLC0415

            return [np.zeros(384, dtype=np.float32) for _ in texts]

    _install_fake_fastembed(monkeypatch, _EmbeddingModel)

    resolved = warm_embedder("some/model")

    assert resolved == str(cache_dir)
    assert embedded == [["warmup"]]  # exactly one warm embed ran
