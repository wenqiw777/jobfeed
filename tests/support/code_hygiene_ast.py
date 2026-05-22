"""AST rules for the Phase 0 code hygiene checker."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

from tests.support.code_hygiene_docstrings import (
    has_documented_parameters,
    has_section,
    has_time_complexity_doc,
    raises_directly,
    returns_value,
)
from tests.support.code_hygiene_types import HygieneViolation

APPLICATION_LAYER_NAMES = {"cli", "services"}
MAX_IF_NESTING_DEPTH = 2


@dataclass(frozen=True)
class _FunctionContext:
    has_time_complexity_doc: bool


@dataclass(frozen=True)
class _ClassContext:
    is_public: bool


class _CodeHygieneVisitor(ast.NodeVisitor):
    def __init__(self, path: Path) -> None:
        self.path = path
        self.violations: list[HygieneViolation] = []
        # These counters track structural depth in the original code. They are
        # reset by the matching visitor method so sibling branches do not affect
        # each other.
        self._loop_depth = 0
        self._if_depth = 0
        # Function depth lets us document public API functions without forcing
        # nested helper functions in tests or closures to carry API docstrings.
        self._function_depth = 0
        self._function_stack: list[_FunctionContext] = []
        # Class context is needed because public methods on private helper
        # classes should not be treated as production API surface.
        self._class_stack: list[_ClassContext] = []

    def visit_For(self, node: ast.For) -> None:
        self._visit_loop(node)

    def visit_AsyncFor(self, node: ast.AsyncFor) -> None:
        self._visit_loop(node)

    def visit_While(self, node: ast.While) -> None:
        self._visit_loop(node)

    def visit_If(self, node: ast.If) -> None:
        self._visit_if(node, increments_depth=True)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        is_public = _is_public_name(node.name)
        if is_public and ast.get_docstring(node) is None:
            self._add_violation(
                node,
                f"public class {node.name} must have a docstring",
            )
        self._class_stack.append(_ClassContext(is_public=is_public))
        self.generic_visit(node)
        self._class_stack.pop()

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.type is None:
            self._add_violation(node, "bare except is not allowed")
        if len(node.body) == 1 and isinstance(node.body[0], ast.Pass):
            self._add_violation(
                node,
                "empty except block containing only pass is not allowed",
            )
        self.generic_visit(node)

    def _visit_if(self, node: ast.If, *, increments_depth: bool) -> None:
        if increments_depth:
            self._if_depth += 1
            if self._if_depth > MAX_IF_NESTING_DEPTH:
                self._add_violation(node, "if nesting deeper than 2 is not allowed")
        for child in node.body:
            self.visit(child)
        self._visit_else_branch(node.orelse)
        if increments_depth:
            self._if_depth -= 1

    def _visit_loop(self, node: ast.For | ast.AsyncFor | ast.While) -> None:
        self._loop_depth += 1
        if self._loop_depth > 1:
            self._check_nested_loop(node)
        self.generic_visit(node)
        self._loop_depth -= 1

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        if _requires_public_docstring(
            node,
            function_depth=self._function_depth,
            class_stack=self._class_stack,
        ):
            self._check_function_docstring(node)
        self._function_stack.append(
            _FunctionContext(
                has_time_complexity_doc=has_time_complexity_doc(node),
            )
        )
        self._function_depth += 1
        self.generic_visit(node)
        self._function_depth -= 1
        self._function_stack.pop()

    def _check_function_docstring(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
    ) -> None:
        docstring = ast.get_docstring(node)
        if docstring is None:
            self._add_violation(
                node,
                f"public function {node.name} must have a docstring",
            )
            return
        if has_documented_parameters(node) and not has_section(docstring, "Args"):
            self._add_violation(node, f"public function {node.name} documents no Args")
        if returns_value(node) and not has_section(docstring, "Returns"):
            self._add_violation(
                node,
                f"public function {node.name} documents no Returns",
            )
        if raises_directly(node) and not has_section(docstring, "Raises"):
            self._add_violation(
                node,
                f"public function {node.name} documents no Raises",
            )

    def _check_nested_loop(self, node: ast.AST) -> None:
        if _is_application_layer_file(self.path):
            self._add_violation(
                node,
                "nested for/while loops are not allowed in application/service code",
            )
            return
        if not self._function_stack:
            self._add_violation(
                node,
                "nested for/while loops outside application/service code must live "
                "in a named helper with documented time complexity",
            )
            return
        # Algorithmic code may need nested loops, but the complexity must be
        # visible where the helper is defined so reviewers can judge the cost.
        if not self._function_stack[-1].has_time_complexity_doc:
            self._add_violation(
                node,
                "nested for/while loops outside application/service code must "
                "document time complexity in the helper docstring",
            )

    def _add_violation(self, node: ast.AST, message: str) -> None:
        self.violations.append(
            HygieneViolation(
                path=self.path,
                line=getattr(node, "lineno", 1),
                message=message,
            )
        )

    def _visit_else_branch(self, orelse: list[ast.stmt]) -> None:
        if len(orelse) == 1 and isinstance(orelse[0], ast.If):
            # Python represents `elif` as an `else: if ...` subtree. Treating
            # that shape as nested would punish flat branch tables.
            self._visit_if(orelse[0], increments_depth=False)
            return
        for child in orelse:
            self.visit(child)


def check_ast_rules(path: Path) -> list[HygieneViolation]:
    """Check module docstrings, public docstrings, nesting, and exceptions.

    Args:
        path: Python source file to parse.

    Returns:
        AST-derived hygiene violations for the file.
    """
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    violations = _check_module_docstring(path, source, tree)
    visitor = _CodeHygieneVisitor(path)
    visitor.visit(tree)
    violations.extend(visitor.violations)
    return violations


def _check_module_docstring(
    path: Path,
    source: str,
    tree: ast.Module,
) -> list[HygieneViolation]:
    if path.name == "__init__.py" and not source.strip():
        return []
    if ast.get_docstring(tree) is not None:
        return []
    return [
        HygieneViolation(
            path=path,
            line=1,
            message="python modules must have a module docstring",
        )
    ]


def _requires_public_docstring(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    *,
    function_depth: int,
    class_stack: list[_ClassContext],
) -> bool:
    if not _is_public_name(node.name):
        return False
    if not class_stack:
        return function_depth == 0
    return class_stack[-1].is_public and function_depth == 0


def _is_public_name(name: str) -> bool:
    return not name.startswith("_")


def _is_application_layer_file(path: Path) -> bool:
    parts = path.parts
    if "jobfeed" not in parts:
        return False
    package_index = len(parts) - 1 - tuple(reversed(parts)).index("jobfeed")
    if package_index + 1 >= len(parts):
        return False
    return parts[package_index + 1] in APPLICATION_LAYER_NAMES
