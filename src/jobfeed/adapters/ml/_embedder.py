"""ONNX (fastembed) embedder for the ML gate (lazy, legacy-parity).

``FastEmbedEmbedder`` wraps a configurable model (default
``all-MiniLM-L6-v2``) to turn a posting's ``title | jd_text`` string into a
normalized 384-d vector. The ``fastembed`` import is deferred to the first
``embed_batch`` call, so this module — and the ``XGBoostGate`` that injects it —
both import AND construct without the ONNX runtime loaded; only an actual embed
materializes it.

fastembed runs ``all-MiniLM-L6-v2`` through onnxruntime (no torch), and its
output is byte-identical (float32) to ``sentence_transformers``'
``encode(..., normalize_embeddings=True)`` — both are L2-normalized (norm=1.0),
so the committed XGBoost model's decisions are unchanged. Dropping torch also
removes the OpenMP-collision segfault risk that the previous backend carried.

Legacy double-truncation is preserved exactly: ``format_input`` slices the JD to
``max_chars - len(title) - 3`` before joining, and ``embed_batch`` re-slices each
text to ``max_chars`` (legacy ``embedder.py`` does both). ``EmbedderProtocol`` is
the structural seam injected into the gate; it lives here (an internal
collaborator), deliberately NOT in ``ports/``.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import numpy as np
import numpy.typing as npt
import structlog

# fastembed addresses ``all-MiniLM-L6-v2`` by its full Hugging Face id. The
# legacy short name is mapped to it so existing ``[ml_gate].embedding_model``
# configs keep working unchanged.
FASTEMBED_MINILM_ID = "sentence-transformers/all-MiniLM-L6-v2"
DEFAULT_MODEL_NAME = "all-MiniLM-L6-v2"
DEFAULT_MAX_CHARS = 2000

_MODEL_NAME_ALIASES = {DEFAULT_MODEL_NAME: FASTEMBED_MINILM_ID}

# Env override + host-native default for the fastembed ONNX weight cache. Without
# an explicit cache_dir, fastembed falls back to ``$TMPDIR/fastembed_cache``,
# which is ephemeral inside a ``docker compose run --rm`` container — re-downloading
# ~90MB every run. We pin it under ~/.cache/jobfeed (the repo's runtime-cache
# convention, NEVER ~/.jobfeed) so host runs persist with zero config, and the
# docker-compose service overrides it via JOBFEED_ML_CACHE_DIR onto a named volume.
ML_CACHE_DIR_ENV = "JOBFEED_ML_CACHE_DIR"


@runtime_checkable
class EmbedderProtocol(Protocol):
    """Structural seam for the gate's text embedder (injectable for tests).

    The gate formats each posting via ``format_input`` then embeds the batch via
    ``embed_batch``; both are part of the collaborator contract.
    """

    def format_input(self, title: str, jd_text: str) -> str:
        """Join a title + JD into the model's single input string.

        Args:
            title: Job title.
            jd_text: Full job-description body.

        Returns:
            The formatted single-string model input.
        """
        ...

    def embed_batch(self, texts: list[str]) -> npt.NDArray[np.float32]:
        """Embed a batch of texts into normalized vectors.

        Args:
            texts: Pre-formatted model-input strings to embed.

        Returns:
            A stacked array of L2-normalized embeddings, one row per text.
        """
        ...


class FastEmbedEmbedder:
    """ONNX (fastembed) embedder with a lazy ``fastembed`` import.

    Construction is cheap and toolchain-free: the configured ``model_name`` is
    stored but the model (and the ``fastembed`` / onnxruntime import) is only
    materialized on the FIRST ``embed_batch`` call. This keeps wiring
    (``XGBoostGate`` -> default embedder) importable and constructible even
    before the ONNX model has been downloaded, deferring the cost to real use.
    """

    def __init__(
        self,
        model_name: str = DEFAULT_MODEL_NAME,
        max_chars: int = DEFAULT_MAX_CHARS,
    ) -> None:
        """Record the model id + char cap WITHOUT loading the model.

        Args:
            model_name: Model id to load on first ``embed_batch``. The legacy
                ``all-MiniLM-L6-v2`` short name is mapped to fastembed's full
                Hugging Face id; any other id is passed through verbatim.
            max_chars: Per-text character cap applied in ``embed_batch`` and
                ``format_input`` (legacy default 2000).
        """
        self._model_name = _MODEL_NAME_ALIASES.get(model_name, model_name)
        self._max_chars = max_chars
        self._model: Any | None = None

    def embed_batch(self, texts: list[str]) -> npt.NDArray[np.float32]:
        """Embed ``texts`` into normalized 384-d vectors (one batch).

        Lazily loads the configured model on first call (the only place the
        ``fastembed`` import happens). Each text is re-sliced to ``max_chars``
        first (legacy behaviour, even though ``format_input`` already truncates
        the JD half).

        fastembed's ``embed`` returns a generator of L2-normalized
        ``np.ndarray`` rows (norm=1.0), matching the legacy
        ``encode(..., normalize_embeddings=True)`` output exactly.

        Args:
            texts: Pre-formatted ``"title | jd"`` strings to embed.

        Returns:
            A ``(len(texts), 384)`` array of L2-normalized embeddings.
        """
        if self._model is None:
            self._model = _load_model(self._model_name)
        truncated = [text[: self._max_chars] for text in texts]
        return np.asarray(list(self._model.embed(truncated)), dtype=np.float32)

    def format_input(self, title: str, jd_text: str) -> str:
        """Join title and JD into the model's input string (legacy truncation).

        Args:
            title: Job title.
            jd_text: Full job-description body.

        Returns:
            ``f"{title} | {jd_text[:max_chars - len(title) - 3]}"`` — the JD half
            is truncated so the joined string stays near ``max_chars``. The slice
            end is clamped at 0 so a title longer than ``max_chars`` truncates the
            JD from the START (never an unintended from-the-end negative slice).
        """
        end = max(0, self._max_chars - len(title) - 3)
        return f"{title} | {jd_text[:end]}"


def _resolve_cache_dir() -> str:
    """Return the stable fastembed weight-cache dir (env override or host default).

    ``$JOBFEED_ML_CACHE_DIR`` wins (the docker-compose service points it at a
    persistent named volume so weights survive ``--rm``, and the default Docker
    image *bakes* the weights here at build time); otherwise the host default
    ``~/.cache/jobfeed/fastembed`` keeps host-native runs persistent with zero
    config. The directory is created if missing so the first run can write.
    """
    override = os.environ.get(ML_CACHE_DIR_ENV)
    cache_dir = (
        Path(override) if override else Path.home() / ".cache" / "jobfeed" / "fastembed"
    )
    cache_dir.mkdir(parents=True, exist_ok=True)
    return str(cache_dir)


def _model_dir_token(model_name: str) -> str:
    """Return the lowercased identity token a model's cache dir must contain.

    fastembed names a model's on-disk dir after its DOWNLOAD source, not the
    user-facing id: the HF mirror ``models--<org>--<repo>`` (e.g.
    ``models--qdrant--all-MiniLM-L6-v2-onnx``) or the GCS layout ``fast-<name>``
    (e.g. ``fast-all-MiniLM-L6-v2``). Both carry the model id's LAST path segment
    verbatim, so that segment (lowercased) is a registry-free, version-tolerant
    token to scope the weight search to THIS model — independent of fastembed's
    exact mirror org / suffix.
    """
    return model_name.rsplit("/", 1)[-1].lower()


def weights_present(cache_dir: str | Path, model_name: str) -> bool:
    """Whether the REQUESTED model's ONNX weights already live under ``cache_dir``.

    A bare "any ``model.onnx`` under the cache" check is WRONG when models share a
    cache dir: a baked default MiniLM would mask a cold cache for a different
    configured ``embedding_model`` and let it silently download. So the search is
    scoped to the requested model — a ``model.onnx`` counts only when an owner
    directory between it and ``cache_dir`` carries this model's
    ``_model_dir_token`` (the HF ``models--<org>--<repo>`` owner or the GCS
    ``fast-<name>`` owner). Robust across both fastembed cache layouts.

    Args:
        cache_dir: Resolved fastembed weight-cache directory.
        model_name: Requested model id to scope the presence check to.

    Returns:
        True iff a ``model.onnx`` for THIS model exists beneath ``cache_dir``.
    """
    root = Path(cache_dir)
    if not root.is_dir():
        return False
    token = _model_dir_token(model_name)
    return any(
        _onnx_belongs_to_model(onnx, root, token) for onnx in root.rglob("model.onnx")
    )


def _onnx_belongs_to_model(onnx: Path, root: Path, token: str) -> bool:
    """Whether an ancestor dir of ``onnx`` (up to ``root``) carries the model token.

    Walks ``onnx``'s ancestors up to (excluding) ``root``, matching ``token``
    against each dir name — so the owner dir is found at any nesting depth.
    """
    for parent in onnx.parents:
        if parent == root:
            return False
        if token in parent.name.lower():
            return True
    return False


def warm_embedder(model_name: str = DEFAULT_MODEL_NAME) -> str:
    """Download + materialize the embedder weights into the resolved cache dir.

    Single source of truth for "put the ONNX weights on disk at the runtime
    cache path" — reused by the ``jobfeed ml-gate fetch`` command and by the
    Docker image's build-time bake, so neither can drift from the path a real
    evaluation run reads. Loads the model (triggering the one-time HF download
    when absent) and runs a tiny embed so the ONNX session is fully realized.

    Args:
        model_name: Model id to warm; defaults to ``all-MiniLM-L6-v2`` (mapped
            to its full HF id like every other call site).

    Returns:
        The resolved cache directory the weights landed in.
    """
    embedder = FastEmbedEmbedder(model_name=model_name)
    embedder.embed_batch(["warmup"])
    return _resolve_cache_dir()


def _load_model(model_name: str) -> Any:
    """Construct the fastembed ``TextEmbedding``, muting its load-time output.

    The ``fastembed`` import is performed here so the surrounding module imports
    cleanly without the ONNX runtime installed. The model's ONNX weights are
    downloaded once from Hugging Face into ``_resolve_cache_dir()`` (a stable
    path / persistent Docker volume, NOT fastembed's ephemeral temp default) and
    reused on every later run, so the download is genuinely one-time per machine.
    The default Docker image bakes the weights here at build time, so the
    canonical path never downloads; a host-native cache miss logs one clear
    line (with a pre-seed hint) BEFORE the download so it is never a surprise.
    """
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    logging.getLogger("huggingface_hub").setLevel(logging.ERROR)
    logging.getLogger("fastembed").setLevel(logging.ERROR)

    cache_dir = _resolve_cache_dir()
    if not weights_present(cache_dir, model_name):
        _warn_one_time_download(model_name, cache_dir)

    import warnings  # noqa: PLC0415

    from fastembed import TextEmbedding  # noqa: PLC0415

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return TextEmbedding(model_name, cache_dir=cache_dir)


def _warn_one_time_download(model_name: str, cache_dir: str) -> None:
    """Emit one structlog line before a cold-cache embedder weight download.

    Fired only on a genuine cache MISS (so the baked Docker image and warm
    host caches stay silent). Surfaces the model id + target dir and points at
    ``jobfeed ml-gate fetch`` so the implicit fetch is visible and pre-seedable
    instead of a mysterious mid-evaluation stall.
    """
    structlog.get_logger("jobfeed").warning(
        "embedder_weights_downloading",
        model=model_name,
        cache_dir=cache_dir,
        hint="one-time per machine; run `jobfeed ml-gate fetch` to pre-seed",
    )


__all__ = [
    "DEFAULT_MAX_CHARS",
    "DEFAULT_MODEL_NAME",
    "FASTEMBED_MINILM_ID",
    "ML_CACHE_DIR_ENV",
    "EmbedderProtocol",
    "FastEmbedEmbedder",
    "warm_embedder",
    "weights_present",
]
