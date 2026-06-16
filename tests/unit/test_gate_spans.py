"""Tests for OTel span integration in the XGBoost ML-gate adapter.

Verifies that xgboost_gate.py imports tracing via the ``observability``
facade (never ``opentelemetry`` directly), and that predict_batch works
correctly with OTel disabled (no-op spans).
"""

from __future__ import annotations

import ast
import shutil
from pathlib import Path

import numpy as np
import pytest

from jobfeed.adapters.ml.xgboost_gate import XGBoostGate
from jobfeed.ports.ml_gate import GateInput

SOURCE_ROOT = Path(__file__).resolve().parents[2] / "src" / "jobfeed"
GATE_PATH = SOURCE_ROOT / "adapters" / "ml" / "xgboost_gate.py"
VALIDATION_PATH = SOURCE_ROOT / "adapters" / "ml" / "_gate_validation.py"

EMBED_DIM = 384
MAX_FILE_LINES = 300
CLEAN_TITLE = "Frontend Engineer"
CLEAN_JD = "Build UIs with React and TypeScript. Write clean code."
HARDFAIL_TITLE = "Sales Manager"
HARDFAIL_JD = "Sell products to enterprise clients. Hit quota."


def _direct_imports(path: Path) -> list[str]:
    """Parse top-level import module names from a Python file.

    Args:
        path: Python source file to parse.

    Returns:
        List of imported module names.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules.append(node.module)
    return modules


def test_xgboost_gate_does_not_import_opentelemetry_directly() -> None:
    """The adapter must use the observability facade, never opentelemetry."""
    imports = _direct_imports(GATE_PATH)
    otel_imports = [m for m in imports if m.startswith("opentelemetry")]
    assert not otel_imports, (
        f"xgboost_gate.py imports opentelemetry directly: {otel_imports}; "
        "use jobfeed.observability.get_tracer instead"
    )


def test_gate_validation_does_not_import_opentelemetry() -> None:
    """The extracted validation module must not import opentelemetry."""
    imports = _direct_imports(VALIDATION_PATH)
    otel_imports = [m for m in imports if m.startswith("opentelemetry")]
    assert not otel_imports, (
        f"_gate_validation.py imports opentelemetry directly: {otel_imports}"
    )


def test_xgboost_gate_imports_observability() -> None:
    """The adapter should import from the observability module."""
    imports = _direct_imports(GATE_PATH)
    assert "jobfeed.observability" in imports


class _FakeEmbedder:
    """Minimal fake embedder for span tests; returns zero-fill vectors."""

    def format_input(self, title: str, jd_text: str) -> str:
        """Format title and JD for embedding.

        Args:
            title: Job title.
            jd_text: Job description text.

        Returns:
            Formatted input string.
        """
        return f"{title} | {jd_text}"

    def embed_batch(self, texts: list[str]) -> np.ndarray:
        """Return zero-fill embeddings.

        Args:
            texts: Input texts to embed.

        Returns:
            Zero-filled embedding matrix.
        """
        return np.zeros((len(texts), EMBED_DIM), dtype=np.float32)


@pytest.fixture()
def _gate(tmp_path: Path) -> XGBoostGate:
    """Build an XGBoostGate with a tiny fixture model and fake embedder.

    Args:
        tmp_path: Temporary directory for model staging.

    Returns:
        Configured XGBoostGate instance.
    """
    fixtures = Path(__file__).resolve().parents[1] / "fixtures"
    shutil.copy(fixtures / "ml_gate_tiny_model.json", tmp_path / "v1.json")
    shutil.copy(fixtures / "ml_gate_tiny_model.meta.json", tmp_path / "v1.meta.json")
    return XGBoostGate(model_dir=tmp_path, embedder=_FakeEmbedder())


async def test_predict_batch_works_with_noop_spans(_gate: XGBoostGate) -> None:
    """predict_batch should work with OTel disabled (no-op spans)."""
    results = await _gate.predict_batch(
        [GateInput(job_id="s1", title=CLEAN_TITLE, jd_text=CLEAN_JD)]
    )
    assert len(results) == 1
    assert results[0].result in ("pass", "fail")
    assert results[0].version == "v1"


async def test_hard_fail_skips_spans(_gate: XGBoostGate) -> None:
    """Hard-failed rows bypass scoring spans entirely."""
    results = await _gate.predict_batch(
        [GateInput(job_id="hf1", title=HARDFAIL_TITLE, jd_text=HARDFAIL_JD)]
    )
    assert len(results) == 1
    assert results[0].result == "fail"
    assert results[0].fail_reason is not None
    assert results[0].score == 0.0


def test_xgboost_gate_under_line_limit() -> None:
    """xgboost_gate.py must stay at or under 300 lines."""
    line_count = len(GATE_PATH.read_text(encoding="utf-8").splitlines())
    assert line_count <= MAX_FILE_LINES, (
        f"xgboost_gate.py is {line_count} lines; must be <= {MAX_FILE_LINES}"
    )
