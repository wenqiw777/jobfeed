"""Focused tests for the Phase 0 code hygiene checker."""

from __future__ import annotations

from pathlib import Path

from tests.support.code_hygiene import (
    PROJECT_ROOT,
    assert_no_hygiene_violations,
    collect_length_warnings,
)
from tests.support.code_hygiene_fixtures import (
    BARE_EXCEPT,
    CLEAN_CODE,
    DEEP_IF_NESTING,
    DOCUMENTED_NESTED_LOOP,
    EMPTY_EXCEPT_PASS,
    FLAT_ELIF_CHAIN,
    INCOMPLETE_FUNCTION_DOCSTRING,
    MISSING_CLASS_DOCSTRING,
    MISSING_FUNCTION_DOCSTRING,
    MISSING_METHOD_DOCSTRING,
    MISSING_MODULE_DOCSTRING,
    SERVICE_NESTED_LOOP,
    UNDOCUMENTED_NESTED_LOOP,
)


def write_source(root: Path, relative_path: str, source: str) -> Path:
    """Write a synthetic production source file for hygiene checker tests.

    Args:
        root: Synthetic `src/jobfeed` package root.
        relative_path: File path relative to `root`.
        source: Python source code to write.

    Returns:
        Path to the written source file.
    """
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")
    return path


def assert_hygiene_error(root: Path, expected_message: str) -> None:
    """Assert that a synthetic package fails with an expected message.

    Args:
        root: Synthetic `src/jobfeed` package root.
        expected_message: Message fragment expected in the assertion.

    Raises:
        AssertionError: If the package passes or fails for the wrong reason.
    """
    try:
        assert_no_hygiene_violations(root)
    except AssertionError as exc:
        assert expected_message in str(exc)
    else:
        raise AssertionError(f"Expected hygiene error: {expected_message}")


def test_code_hygiene_passes_for_current_production_code() -> None:
    """Production code should satisfy all current hygiene gates."""
    assert_no_hygiene_violations()


def test_code_hygiene_checker_code_is_hygienic() -> None:
    """The hygiene checker should not be exempt from its own structural rules."""
    assert_no_hygiene_violations(Path(__file__))
    assert_no_hygiene_violations(PROJECT_ROOT / "tests" / "support")


def test_hygiene_check_fails_on_service_nested_loop_fixture(
    tmp_path: Path,
) -> None:
    """Nested loops should fail in application and service layers.

    Args:
        tmp_path: Temporary package root for the synthetic fixture.
    """
    root = tmp_path / "src" / "jobfeed"
    write_source(root, "services/nested_loop.py", SERVICE_NESTED_LOOP)

    assert_hygiene_error(root, "application/service code")


def test_hygiene_check_allows_documented_algorithm_nested_loop(
    tmp_path: Path,
) -> None:
    """Algorithmic nested loops are allowed when complexity is documented.

    Args:
        tmp_path: Temporary package root for the synthetic fixture.
    """
    root = tmp_path / "src" / "jobfeed"
    write_source(root, "domain/pairs.py", DOCUMENTED_NESTED_LOOP)

    assert_no_hygiene_violations(root)


def test_hygiene_check_fails_undocumented_algorithm_nested_loop(
    tmp_path: Path,
) -> None:
    """Algorithmic nested loops should fail without complexity documentation.

    Args:
        tmp_path: Temporary package root for the synthetic fixture.
    """
    root = tmp_path / "src" / "jobfeed"
    write_source(root, "domain/pairs.py", UNDOCUMENTED_NESTED_LOOP)

    assert_hygiene_error(root, "document time complexity")


def test_hygiene_check_fails_on_missing_module_docstring(
    tmp_path: Path,
) -> None:
    """Production modules should describe their responsibility.

    Args:
        tmp_path: Temporary package root for the synthetic fixture.
    """
    root = tmp_path / "src" / "jobfeed"
    write_source(root, "missing_module_doc.py", MISSING_MODULE_DOCSTRING)

    assert_hygiene_error(root, "module docstring")


def test_hygiene_check_fails_on_missing_public_function_docstring(
    tmp_path: Path,
) -> None:
    """Public production functions should document their contract.

    Args:
        tmp_path: Temporary package root for the synthetic fixture.
    """
    root = tmp_path / "src" / "jobfeed"
    write_source(root, "missing_function_doc.py", MISSING_FUNCTION_DOCSTRING)

    assert_hygiene_error(root, "public function find_positive must have a docstring")


def test_hygiene_check_fails_on_incomplete_public_function_docstring(
    tmp_path: Path,
) -> None:
    """Function docstrings should include sections required by the code.

    Args:
        tmp_path: Temporary package root for the synthetic fixture.
    """
    root = tmp_path / "src" / "jobfeed"
    write_source(root, "incomplete_function_doc.py", INCOMPLETE_FUNCTION_DOCSTRING)

    assert_hygiene_error(root, "documents no Args")
    assert_hygiene_error(root, "documents no Returns")
    assert_hygiene_error(root, "documents no Raises")


def test_hygiene_check_fails_on_missing_public_class_docstring(
    tmp_path: Path,
) -> None:
    """Public production classes should document their role.

    Args:
        tmp_path: Temporary package root for the synthetic fixture.
    """
    root = tmp_path / "src" / "jobfeed"
    write_source(root, "missing_class_doc.py", MISSING_CLASS_DOCSTRING)

    assert_hygiene_error(root, "public class Allocator must have a docstring")


def test_hygiene_check_fails_on_missing_public_method_docstring(
    tmp_path: Path,
) -> None:
    """Public methods on public classes should document their contract.

    Args:
        tmp_path: Temporary package root for the synthetic fixture.
    """
    root = tmp_path / "src" / "jobfeed"
    write_source(root, "missing_method_doc.py", MISSING_METHOD_DOCSTRING)

    assert_hygiene_error(root, "public function allocate must have a docstring")


def test_hygiene_check_passes_on_synthetic_clean_code(tmp_path: Path) -> None:
    """A simple well-documented function should pass.

    Args:
        tmp_path: Temporary package root for the synthetic fixture.
    """
    root = tmp_path / "src" / "jobfeed"
    write_source(root, "clean.py", CLEAN_CODE)

    assert_no_hygiene_violations(root)


def test_hygiene_check_treats_flat_elif_chain_as_clean(tmp_path: Path) -> None:
    """A flat elif chain should not count as nested if depth.

    Args:
        tmp_path: Temporary package root for the synthetic fixture.
    """
    root = tmp_path / "src" / "jobfeed"
    write_source(root, "elif_chain.py", FLAT_ELIF_CHAIN)

    assert_no_hygiene_violations(root)


def test_hygiene_check_fails_on_deep_if_nesting(tmp_path: Path) -> None:
    """If nesting deeper than two levels should fail.

    Args:
        tmp_path: Temporary package root for the synthetic fixture.
    """
    root = tmp_path / "src" / "jobfeed"
    write_source(root, "deep_if.py", DEEP_IF_NESTING)

    assert_hygiene_error(root, "if nesting deeper than 2")


def test_hygiene_check_fails_on_bare_except(tmp_path: Path) -> None:
    """Bare except handlers should fail the hygiene gate.

    Args:
        tmp_path: Temporary package root for the synthetic fixture.
    """
    root = tmp_path / "src" / "jobfeed"
    write_source(root, "bare_except.py", BARE_EXCEPT)

    assert_hygiene_error(root, "bare except is not allowed")


def test_hygiene_check_fails_on_empty_except_pass(tmp_path: Path) -> None:
    """Empty except blocks containing only pass should fail.

    Args:
        tmp_path: Temporary package root for the synthetic fixture.
    """
    root = tmp_path / "src" / "jobfeed"
    write_source(root, "empty_except_pass.py", EMPTY_EXCEPT_PASS)

    assert_hygiene_error(root, "empty except block containing only pass")


def test_length_violation_blocks_non_exempt_file(tmp_path: Path) -> None:
    """Files over 300 lines outside the adapter layer fail the gate.

    Args:
        tmp_path: Temporary package root for the synthetic fixture.
    """
    root = tmp_path / "src" / "jobfeed"
    padding = "\n".join(f"# padding {index}" for index in range(310))
    write_source(root, "too_long.py", f"{CLEAN_CODE}\n{padding}\n")

    assert_hygiene_error(root, "exceeds 300 lines")
    warnings = collect_length_warnings(root)
    assert len(warnings) == 1


def test_length_gate_exempts_store_adapter(tmp_path: Path) -> None:
    """The store/migration adapter layer is exempt from the file-length gate.

    Args:
        tmp_path: Temporary package root for the synthetic fixture.
    """
    root = tmp_path / "src" / "jobfeed"
    padding = "\n".join(f"# padding {index}" for index in range(310))
    write_source(root, "adapters/store/big_store.py", f"{CLEAN_CODE}\n{padding}\n")

    assert_no_hygiene_violations(root)


def test_length_gate_exempts_ml_features(tmp_path: Path) -> None:
    """domain/ml_features.py is exempt from the file-length gate.

    Args:
        tmp_path: Temporary package root for the synthetic fixture.
    """
    root = tmp_path / "src" / "jobfeed"
    padding = "\n".join(f"# padding {index}" for index in range(310))
    write_source(root, "domain/ml_features.py", f"{CLEAN_CODE}\n{padding}\n")

    assert_no_hygiene_violations(root)
