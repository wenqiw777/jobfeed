"""Import-boundary tests for the hexagonal Phase 0 architecture."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = PROJECT_ROOT / "src" / "jobfeed"
DOMAIN_ALLOWED_IMPORTS = {
    "__future__",
    "collections.abc",
    "dataclasses",
    "datetime",
    "enum",
    "hashlib",
    "json",
    "re",
    "typing",
}
ADAPTER_IMPORT_PREFIX = "jobfeed.adapters"
SERVICE_FORBIDDEN_IMPORTS = (ADAPTER_IMPORT_PREFIX, "jobfeed.config", "jobfeed.cli")


@dataclass(frozen=True)
class ImportReference:
    """A parsed import with enough context for readable assertion failures."""

    path: Path
    line: int
    module: str


def test_domain_does_not_import_frameworks_or_io_clients() -> None:
    """Domain modules should stay pure and framework-independent."""
    violations = [
        reference
        for reference in imports_under(SOURCE_ROOT / "domain")
        if not is_domain_import_allowed(reference.module)
    ]

    assert not violations, format_violations(violations)


def test_ports_do_not_import_adapters() -> None:
    """Ports define contracts and must not depend on adapter implementations."""
    violations = [
        reference
        for reference in imports_under(SOURCE_ROOT / "ports")
        if reference.module.startswith(ADAPTER_IMPORT_PREFIX)
    ]

    assert not violations, format_violations(violations)


def test_services_do_not_import_infrastructure_modules() -> None:
    """Services orchestrate ports and domain logic without infrastructure."""
    violations = [
        reference
        for reference in imports_under(SOURCE_ROOT / "services")
        if is_service_import_forbidden(reference.module)
    ]

    assert not violations, format_violations(violations)


def test_web_does_not_import_adapters() -> None:
    """Web modules compose via jobfeed.cli.create_app, never adapters directly."""
    violations = [
        reference
        for reference in imports_under(SOURCE_ROOT / "web")
        if reference.module.startswith(ADAPTER_IMPORT_PREFIX)
    ]

    assert not violations, format_violations(violations)


# Browser automation is forbidden outside the LinkedIn SessionSource surface
# (CLAUDE.md Phase 4 amendment). Only this file may import Playwright, and only
# lazily (via importlib) so non-LinkedIn scans never hard-depend on the browser.
PLAYWRIGHT_IMPORT_ALLOWLIST = frozenset({"adapters/sources/linkedin.py"})


def test_playwright_is_confined_and_lazily_imported() -> None:
    """Playwright may only be imported lazily, from the LinkedIn adapter.

    Targets the import MECHANISM (not prose mentions): a static ``import
    playwright`` is forbidden everywhere so the browser stack is never a hard
    dependency, and the lazy ``importlib.import_module("playwright...")`` site
    is allowed only inside the LinkedIn adapter.
    """
    static_imports: list[str] = []
    dynamic_uses: list[str] = []
    for path in python_files(SOURCE_ROOT):
        rel = path.relative_to(SOURCE_ROOT).as_posix()
        text = path.read_text(encoding="utf-8")
        if any(
            line.strip().startswith(("import playwright", "from playwright"))
            for line in text.splitlines()
        ):
            static_imports.append(rel)
        if (
            'import_module("playwright' in text
            and rel not in PLAYWRIGHT_IMPORT_ALLOWLIST
        ):
            dynamic_uses.append(rel)

    assert not static_imports, f"Playwright must be imported lazily: {static_imports}"
    assert not dynamic_uses, f"Playwright imported outside allowlist: {dynamic_uses}"


def imports_under(root: Path) -> list[ImportReference]:
    """Parse Python imports under a source directory.

    Args:
        root: Package directory whose Python files should be scanned.

    Returns:
        Import references with path, source line, and imported module name.
    """
    references: list[ImportReference] = []
    for path in python_files(root):
        references.extend(imports_in_file(path))
    return references


def imports_in_file(path: Path) -> list[ImportReference]:
    """Parse direct import statements from one Python file.

    Args:
        path: Python source file to parse with `ast`.

    Returns:
        Import references found in the file.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    references: list[ImportReference] = []
    for node in ast.walk(tree):
        references.extend(imports_from_node(path, node))
    return references


def imports_from_node(path: Path, node: ast.AST) -> list[ImportReference]:
    """Extract import references from one AST node.

    Args:
        path: Source file path used for diagnostics.
        node: AST node to inspect.

    Returns:
        Import references represented by the node, or an empty list.
    """
    if isinstance(node, ast.Import):
        return [
            ImportReference(path=path, line=node.lineno, module=alias.name)
            for alias in node.names
        ]
    if isinstance(node, ast.ImportFrom):
        return imports_from_importfrom(path, node)
    return []


def imports_from_importfrom(path: Path, node: ast.ImportFrom) -> list[ImportReference]:
    """Extract module names from a `from ... import ...` AST node.

    Args:
        path: Source file path used for diagnostics.
        node: ImportFrom AST node to inspect.

    Returns:
        Import references for absolute imports only.
    """
    if node.level > 0 or node.module is None:
        return []
    return [ImportReference(path=path, line=node.lineno, module=node.module)]


def python_files(root: Path) -> list[Path]:
    """List Python source files under a root in stable order.

    Args:
        root: Package directory to scan.

    Returns:
        Sorted Python file paths.
    """
    return sorted(path for path in root.rglob("*.py") if path.is_file())


def import_root(module: str) -> str:
    """Return the top-level module segment for an import path.

    Args:
        module: Fully qualified import path.

    Returns:
        Top-level import segment.
    """
    return module.split(".", maxsplit=1)[0]


def is_domain_import_allowed(module: str) -> bool:
    """Return whether a module import is allowed from the pure domain layer.

    Args:
        module: Fully qualified import path.

    Returns:
        True when the import is stdlib-only or another domain module.
    """
    return module in DOMAIN_ALLOWED_IMPORTS or module.startswith("jobfeed.domain")


def is_service_import_forbidden(module: str) -> bool:
    """Return whether a service import crosses into infrastructure packages.

    Args:
        module: Fully qualified import path.

    Returns:
        True when the service layer imports forbidden infrastructure.
    """
    return any(
        module == forbidden or module.startswith(f"{forbidden}.")
        for forbidden in SERVICE_FORBIDDEN_IMPORTS
    )


def format_violations(violations: list[ImportReference]) -> str:
    """Format import-boundary violations for assertion output.

    Args:
        violations: Import references that violated a boundary.

    Returns:
        Human-readable multi-line failure message.
    """
    return "\n".join(
        f"{reference.path.relative_to(PROJECT_ROOT)}:"
        f"{reference.line} imports {reference.module}"
        for reference in violations
    )
