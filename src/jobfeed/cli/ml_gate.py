"""Click commands for the Phase 5 ML evaluation gate.

``jobfeed ml-gate info`` reports the committed model's identity and headline
training metrics by reading ONLY the ``v*.meta.json`` sidecar plus the model
filename (the version is the file stem). It never loads the XGBoost Booster or
the ONNX embedder — a deliberate constraint so the command stays fast and cheap.

``ml-gate train`` is intentionally omitted in this phase: training is an
offline, toolchain-heavy workflow run outside the CLI, and the model is
committed in-repo as a required asset (Rev3). It can be added later if needed.
"""

from __future__ import annotations

import json
from pathlib import Path

import click

from jobfeed.config import Settings
from jobfeed.domain.filtering import HardFilters
from jobfeed.ports.ml_gate import MLGate

DEFAULT_MODEL_DIR = "models/ml_gate"
_META_KEYS = ("recall_pos", "precision_pos", "f1", "train_size")

# Sentinel model_dir that diverts the real XGBoostGate to the lightweight
# MockGate. Mirrors how `mock/<model>` selects MockLLM in the LLM factory, so
# dev/test runs can exercise the gate path without loading xgboost or the ONNX
# embedder (and without the one-time HuggingFace model download).
MOCK_MODEL_DIR = "mock"


@click.group(
    name="ml-gate",
    help=(
        "Inspect the committed ML evaluation gate. "
        "(`ml-gate train` is intentionally omitted — training runs offline.)"
    ),
)
def ml_gate() -> None:
    """Group for ML-gate inspection commands."""


@ml_gate.command(name="info", help="Show the committed ML-gate model + metrics.")
@click.option(
    "--model-dir",
    "model_dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=DEFAULT_MODEL_DIR,
    show_default=True,
    help="Directory holding the v*.json model and v*.meta.json sidecar.",
)
def info(model_dir: Path) -> None:
    """Print the model version, threshold, dir, and headline meta metrics.

    Reads only the model filename (for the version) and its meta JSON. The
    Booster and embedding toolchain are never imported.

    Args:
        model_dir: Directory holding the v*.json model + v*.meta.json sidecar.

    Raises:
        click.ClickException: If the directory holds no v*.json model.
    """
    version = _latest_model_version(model_dir)
    meta = _read_meta(model_dir, version)
    click.echo(f"version:    {version}")
    click.echo(f"threshold:  {meta.get('threshold', 'unknown')}")
    click.echo(f"model_dir:  {model_dir}")
    for key in _META_KEYS:
        click.echo(f"{key}:  {meta.get(key, 'unknown')}")


def _latest_model_version(model_dir: Path) -> str:
    """Return the stem of the lexicographically-latest v*.json model.

    Args:
        model_dir: Directory to scan for ``v*.json`` model files.

    Returns:
        The latest model file's stem (used as the model version).

    Raises:
        click.ClickException: If no ``v*.json`` model file is present.
    """
    models = sorted(
        path
        for path in model_dir.glob("v*.json")
        if not path.name.endswith(".meta.json")
    )
    if not models:
        raise click.ClickException(f"No ML-gate model (v*.json) found in {model_dir}")
    return models[-1].stem


def _read_meta(model_dir: Path, version: str) -> dict[str, object]:
    """Read the model's ``v*.meta.json`` sidecar as a plain dict.

    Args:
        model_dir: Directory holding the meta sidecar.
        version: Model version (file stem) whose sidecar to read.

    Returns:
        Parsed meta mapping, or an empty dict when the sidecar is absent.
    """
    meta_path = model_dir / f"{version}.meta.json"
    if not meta_path.exists():
        return {}
    meta: dict[str, object] = json.loads(meta_path.read_text(encoding="utf-8"))
    return meta


@ml_gate.command(
    name="fetch",
    help="Pre-download the embedder ONNX weights into the local cache dir.",
)
@click.option(
    "--embedding-model",
    "embedding_model",
    default=None,
    help="Embedder model id to fetch (defaults to the built-in all-MiniLM-L6-v2).",
)
def fetch(embedding_model: str | None) -> None:
    """Download/warm the embedder weights into the resolved cache, offline-ready.

    Gives host-native users an explicit pre-seed step instead of an implicit
    mid-evaluation download: it materializes the ONNX ``all-MiniLM-L6-v2``
    weights into ``$JOBFEED_ML_CACHE_DIR`` (else ``~/.cache/jobfeed/fastembed``)
    and prints where they landed plus the on-disk size. The default Docker image
    already bakes these at build time, so this is for the host-native path.

    The ``fastembed`` import is deferred into the warm call, so ``ml-gate fetch``
    is the only ``ml-gate`` subcommand that loads the embedder toolchain.

    Args:
        embedding_model: Embedder model id to fetch, or ``None`` for the default.
    """
    from jobfeed.adapters.ml._embedder import (  # noqa: PLC0415
        DEFAULT_MODEL_NAME,
        warm_embedder,
    )

    model_name = embedding_model or DEFAULT_MODEL_NAME
    click.echo(f"Fetching embedder weights for {model_name} ...")
    cache_dir = warm_embedder(model_name)
    click.echo(f"weights ready: {cache_dir}")
    click.echo(f"cache size:    {_dir_size_human(Path(cache_dir))}")


_BYTES_PER_UNIT = 1024.0
_SIZE_UNITS = ("B", "KiB", "MiB", "GiB")


def _dir_size_human(path: Path) -> str:
    """Return the human-readable on-disk size of all real files under ``path``.

    Sums ``st_size`` over regular files, SKIPPING symlinks: fastembed stores the
    weights once in ``blobs/`` and exposes them via ``snapshots/.../model.onnx``
    symlinks, so counting both (following links) would ~double the figure. We
    count blobs once and ignore the links — matching ``du`` — then format it as
    MiB/GiB for the ``fetch`` summary line.

    Args:
        path: Directory whose recursive real-file size to total.

    Returns:
        A short ``"<n> <unit>"`` size string (e.g. ``"87.0 MiB"``).
    """
    total = sum(
        p.stat(follow_symlinks=False).st_size
        for p in path.rglob("*")
        if p.is_file() and not p.is_symlink()
    )
    size = float(total)
    for unit in _SIZE_UNITS:
        if size < _BYTES_PER_UNIT or unit == _SIZE_UNITS[-1]:
            return f"{size:.1f} {unit}"
        size /= _BYTES_PER_UNIT
    return f"{size:.1f} {_SIZE_UNITS[-1]}"  # pragma: no cover - loop returns first


# ---- evaluate-command gate / filter construction (lazy, toolchain-free) ----


def build_hard_filters(settings: Settings) -> HardFilters:
    """Build the pure-domain hard filters from config.

    Always built (no toolchain needed); ``apply_hard_filters`` is a no-op when
    every list is empty, so a config without ``[hard_filters]`` is harmless.

    Args:
        settings: Loaded application settings.

    Returns:
        Domain HardFilters derived from ``settings.hard_filters``.
    """
    return settings.hard_filters.to_domain()


def needs_ml_gate(stage: str, limit: int | None, *, ml_gate_enabled: bool) -> bool:
    """Whether a run can reach the gate-using Stage-A funnel.

    Build the gate (and load xgboost) only when Stage A both runs and can claim
    work: the gate is enabled, Stage A is selected (``stage != "b"``), and the
    limit allows >=1 job (``None`` = default 100, ``0`` = zero). A Stage-B-only
    or ``--limit 0`` run never gates, so building the gate there is pure waste.
    Mirrors the funnel's gating conditions and the Stage-A ``limit <= 0``
    short-circuit; hard filters stay always-on (pure domain).

    Args:
        stage: Selected evaluate stage ("a", "b", or "both").
        limit: Per-stage job limit (``None`` = default, ``0`` = zero).
        ml_gate_enabled: Whether ``scoring.ml_gate_enabled`` is set.

    Returns:
        True only when the gate should be built for this run.
    """
    return ml_gate_enabled and stage != "b" and (limit is None or limit > 0)


def build_ml_gate(settings: Settings, *, allow_disabled: bool = False) -> MLGate | None:
    """Build the ML gate only when enabled; keep all ML imports lazy.

    Returns None when ``scoring.ml_gate_enabled`` is false so that ``xgboost`` /
    ``fastembed`` are never imported. When enabled, a ``mock``
    ``ml_gate.model_dir`` sentinel selects the deterministic MockGate (dev/test
    escape hatch); otherwise the real XGBoostGate is constructed with the
    default fastembed (ONNX) embedder. fastembed is a core dependency, so the
    real gate is always importable — no preflight check is needed.

    Args:
        settings: Loaded application settings.

    Returns:
        An MLGate implementation, or None when the gate is disabled.
    """
    if not settings.scoring.ml_gate_enabled and not allow_disabled:
        return None
    ml = settings.ml_gate
    if ml.model_dir == MOCK_MODEL_DIR:
        from jobfeed.adapters.ml.mock import MockGate  # noqa: PLC0415

        return MockGate()
    from jobfeed.adapters.ml.xgboost_gate import (  # noqa: PLC0415
        EmbedderConfig,
        XGBoostGate,
    )

    return XGBoostGate(
        model_dir=ml.model_dir,
        model_version=ml.model_version,
        threshold_override=ml.threshold_override,
        embedder_config=EmbedderConfig(
            max_chars=ml.embedding_max_chars,
            model_name=ml.embedding_model,
        ),
    )


__all__ = ["build_hard_filters", "build_ml_gate", "ml_gate", "needs_ml_gate"]
