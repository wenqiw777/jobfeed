"""Docstring contract helpers for the code hygiene checker."""

from __future__ import annotations

import ast

IMPLICIT_RECEIVER_NAMES = {"self", "cls"}


def has_time_complexity_doc(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Check whether a function docstring documents algorithmic complexity.

    Args:
        node: Function node whose docstring should be inspected.

    Returns:
        True when the function docstring contains an accepted complexity marker.
    """
    docstring = ast.get_docstring(node)
    if docstring is None:
        return False
    normalized = docstring.lower()
    return "time complexity" in normalized or "complexity:" in normalized


def has_documented_parameters(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Check whether a function has parameters that need documentation.

    Args:
        node: Function node whose signature should be inspected.

    Returns:
        True when the callable accepts non-receiver parameters.
    """
    parameters = [
        arg.arg
        for arg in node.args.posonlyargs + node.args.args + node.args.kwonlyargs
        if arg.arg not in IMPLICIT_RECEIVER_NAMES
    ]
    return bool(parameters or node.args.vararg or node.args.kwarg)


def has_section(docstring: str, section_name: str) -> bool:
    """Check whether a docstring contains a named contract section.

    Args:
        docstring: Docstring content to inspect.
        section_name: Section heading without the trailing colon.

    Returns:
        True when the section heading appears in the docstring.
    """
    return f"{section_name}:" in docstring


def returns_value(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Check whether a function returns a value that should be documented.

    Args:
        node: Function node whose return annotation and body should be inspected.

    Returns:
        True when the function annotation or a return statement yields a value.
    """
    if node.returns is not None and not _is_none_annotation(node.returns):
        return True
    return any(
        isinstance(child, ast.Return) and child.value is not None
        for child in iter_function_body_nodes(node)
    )


def raises_directly(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Check whether a function directly raises an exception.

    Args:
        node: Function node whose body should be inspected.

    Returns:
        True when a raise statement appears in the function body.
    """
    return any(isinstance(child, ast.Raise) for child in iter_function_body_nodes(node))


def iter_function_body_nodes(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> list[ast.AST]:
    """Return body nodes while ignoring nested scopes.

    Args:
        node: Function node whose direct body tree should be walked.

    Returns:
        Descendant AST nodes that belong to this function's own execution body.
    """
    descendants: list[ast.AST] = []
    stack: list[ast.AST] = list(reversed(node.body))
    while stack:
        child = stack.pop()
        if child is not node and isinstance(
            child,
            ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef | ast.Lambda,
        ):
            continue
        descendants.append(child)
        stack.extend(reversed(list(ast.iter_child_nodes(child))))
    return descendants


def _is_none_annotation(annotation: ast.expr) -> bool:
    return isinstance(annotation, ast.Constant) and annotation.value is None
