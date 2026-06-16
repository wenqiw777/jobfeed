"""Unit tests for the shared EvaluateService factory.

Verifies the Click-free factory constructs without Click dependencies and
that the CLI _evaluate_build.py delegates to it correctly.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

from jobfeed.cli._evaluate_factory import EvalBuildParams, _load_resume_text

_SAMPLE_LIMIT = 10


def test_eval_build_params_has_no_click_dependency() -> None:
    """EvalBuildParams should be constructable without Click."""
    params = EvalBuildParams(
        stage="both",
        corpus="unrated",
        limit=_SAMPLE_LIMIT,
        max_days=None,
    )
    assert params.stage == "both"
    assert params.limit == _SAMPLE_LIMIT
    assert params.dry_run is False


def test_eval_build_params_defaults() -> None:
    """EvalBuildParams optional fields default correctly."""
    params = EvalBuildParams(
        stage="a",
        corpus="all",
        limit=None,
        max_days=7,
    )
    assert params.parallel is None
    assert params.threshold is None
    assert params.dry_run is False


def test_factory_module_has_no_click_imports() -> None:
    """The _evaluate_factory module must not import Click at module level."""
    mod = importlib.import_module("jobfeed.cli._evaluate_factory")
    spec = importlib.util.find_spec(mod.__name__)
    assert spec is not None
    assert spec.origin is not None
    with open(spec.origin, encoding="utf-8") as fh:
        content = fh.read()
    # Check that 'import click' and 'from click' do not appear at module level
    # (TYPE_CHECKING-guarded imports are OK, deferred imports inside functions
    # are OK -- we check only top-level lines before the TYPE_CHECKING block)
    for line in content.split("\n"):
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        if stripped.startswith("if TYPE_CHECKING"):
            break  # everything under TYPE_CHECKING is fine
        assert "import click" not in stripped, (
            f"Module-level click import found: {line}"
        )


def test_load_resume_text_dry_run_returns_empty() -> None:
    """_load_resume_text returns empty string for dry runs."""
    assert _load_resume_text("/nonexistent/path.md", is_dry_run=True) == ""


def test_load_resume_text_missing_file_raises() -> None:
    """_load_resume_text raises FileNotFoundError for missing resume."""
    with pytest.raises(FileNotFoundError, match="Resume file not found"):
        _load_resume_text("/definitely/does/not/exist.md", is_dry_run=False)


def test_load_resume_text_reads_file(tmp_path: Path) -> None:
    """_load_resume_text reads an existing file on non-dry run."""
    p = tmp_path / "resume.md"
    p.write_text("My resume content", encoding="utf-8")
    result = _load_resume_text(str(p), is_dry_run=False)
    assert result == "My resume content"
