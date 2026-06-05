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
    persistent named volume so weights survive ``--rm``); otherwise the host
    default ``~/.cache/jobfeed/fastembed`` keeps host-native runs persistent with
    zero config. The directory is created if missing so the first run can write.
    """
    override = os.environ.get(ML_CACHE_DIR_ENV)
    cache_dir = (
        Path(override) if override else Path.home() / ".cache" / "jobfeed" / "fastembed"
    )
    cache_dir.mkdir(parents=True, exist_ok=True)
    return str(cache_dir)


def _load_model(model_name: str) -> Any:
    """Construct the fastembed ``TextEmbedding``, muting its load-time output.

    The ``fastembed`` import is performed here so the surrounding module imports
    cleanly without the ONNX runtime installed. The model's ONNX weights are
    downloaded once from Hugging Face into ``_resolve_cache_dir()`` (a stable
    path / persistent Docker volume, NOT fastembed's ephemeral temp default) and
    reused on every later run, so the download is genuinely one-time per machine.
    """
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    logging.getLogger("huggingface_hub").setLevel(logging.ERROR)
    logging.getLogger("fastembed").setLevel(logging.ERROR)

    import warnings  # noqa: PLC0415

    from fastembed import TextEmbedding  # noqa: PLC0415

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return TextEmbedding(model_name, cache_dir=_resolve_cache_dir())


__all__ = [
    "DEFAULT_MAX_CHARS",
    "DEFAULT_MODEL_NAME",
    "FASTEMBED_MINILM_ID",
    "ML_CACHE_DIR_ENV",
    "EmbedderProtocol",
    "FastEmbedEmbedder",
]
