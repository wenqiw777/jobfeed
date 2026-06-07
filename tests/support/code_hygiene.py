"""File traversal wrapper for the Phase 0 code hygiene checker."""

from __future__ import annotations

from pathlib import Path

from tests.support.code_hygiene_ast import check_ast_rules
from tests.support.code_hygiene_types import HygieneViolation

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PRODUCTION_ROOT = PROJECT_ROOT / "src" / "jobfeed"
MAX_PYTHON_FILE_LINES = 300

# A few files are exempt from the blocking file-length gate, where shredding
# them into <=300-line fragments harms readability more than it helps:
#   * the persistence/migration adapter layer (/adapters/store/ + cli/migrate.py)
#     -- a full JobStore implementation per backend is inherently large;
#   * domain/ml_features.py -- the ML-gate vocab name lists and the compiled
#     regex tables that index them must stay in lockstep in one file.
# Every other layer stays bound by MAX_PYTHON_FILE_LINES. Documented in
# docs/engineering-standards.md.
_LENGTH_EXEMPT_SUBSTR = "/adapters/store/"
_LENGTH_EXEMPT_SUFFIXES = ("/cli/migrate.py", "/domain/ml_features.py")


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
        violations.extend(check_ast_rules(path))
    return violations


def collect_length_warnings(
    root: Path = DEFAULT_PRODUCTION_ROOT,
) -> list[HygieneViolation]:
    """Collect file-length warnings (soft threshold, non-blocking).

    Args:
        root: Python file or directory tree to scan.

    Returns:
        Warnings for files exceeding the soft line limit.
    """
    warnings: list[HygieneViolation] = []
    for path in _python_files(root):
        warnings.extend(_check_file_length(path))
    return warnings


def assert_no_hygiene_violations(root: Path = DEFAULT_PRODUCTION_ROOT) -> None:
    """Assert that a Python package tree satisfies the Phase 0 hygiene gates.

    Args:
        root: Python file or directory tree to scan.

    Raises:
        AssertionError: If any hygiene violation is found.
    """
    violations = collect_hygiene_violations(root)
    violations.extend(collect_length_violations(root))
    if violations:
        formatted = "\n".join(violation.format() for violation in violations)
        raise AssertionError(f"Code hygiene violations found:\n{formatted}")


def _is_length_exempt(path: Path) -> bool:
    posix = path.as_posix()
    return _LENGTH_EXEMPT_SUBSTR in posix or posix.endswith(_LENGTH_EXEMPT_SUFFIXES)


def collect_length_violations(
    root: Path = DEFAULT_PRODUCTION_ROOT,
) -> list[HygieneViolation]:
    """Collect blocking file-length violations, excluding exempt layers.

    Args:
        root: Python file or directory tree to scan.

    Returns:
        File-length violations for non-exempt files.
    """
    violations: list[HygieneViolation] = []
    for path in _python_files(root):
        if _is_length_exempt(path):
            continue
        violations.extend(_check_file_length(path))
    return violations


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
                f"file exceeds {MAX_PYTHON_FILE_LINES} lines "
                f"({line_count}); review whether it should be split"
            ),
        )
    ]
