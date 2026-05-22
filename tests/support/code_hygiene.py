"""File traversal wrapper for the Phase 0 code hygiene checker."""

from __future__ import annotations

from pathlib import Path

from tests.support.code_hygiene_ast import check_ast_rules
from tests.support.code_hygiene_types import HygieneViolation

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PRODUCTION_ROOT = PROJECT_ROOT / "src" / "jobfeed"
MAX_PYTHON_FILE_LINES = 300


def collect_hygiene_violations(
    root: Path = DEFAULT_PRODUCTION_ROOT,
) -> list[HygieneViolation]:
    """Collect all hygiene violations under a Python package root.

    Args:
        root: Python file or directory tree to scan.

    Returns:
        Hygiene violations sorted by file traversal order.
    """
    violations: list[HygieneViolation] = []
    for path in _python_files(root):
        violations.extend(_check_file_length(path))
        violations.extend(check_ast_rules(path))
    return violations


def assert_no_hygiene_violations(root: Path = DEFAULT_PRODUCTION_ROOT) -> None:
    """Assert that a Python package tree satisfies the Phase 0 hygiene gates.

    Args:
        root: Python file or directory tree to scan.

    Raises:
        AssertionError: If any hygiene violation is found.
    """
    violations = collect_hygiene_violations(root)
    if violations:
        formatted = "\n".join(violation.format() for violation in violations)
        raise AssertionError(f"Code hygiene violations found:\n{formatted}")


def _python_files(root: Path) -> list[Path]:
    if not root.exists():
        return []
    if root.is_file():
        return [root] if root.suffix == ".py" else []
    return sorted(path for path in root.rglob("*.py") if path.is_file())


def _check_file_length(path: Path) -> list[HygieneViolation]:
    line_count = len(path.read_text(encoding="utf-8").splitlines())
    if line_count <= MAX_PYTHON_FILE_LINES:
        return []
    return [
        HygieneViolation(
            path=path,
            line=MAX_PYTHON_FILE_LINES + 1,
            message=(
                "python files must be "
                f"{MAX_PYTHON_FILE_LINES} lines or fewer in Phase 0; "
                "split the module instead of deleting useful comments"
            ),
        )
    ]
