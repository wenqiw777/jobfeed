"""Shared company/title normalization helpers for store adapters."""

from __future__ import annotations

import re

_NORM_RE = re.compile(r"[^a-z0-9]+")

_CORP_SUFFIXES = frozenset(
    {
        "inc",
        "incorporated",
        "corp",
        "corporation",
        "llc",
        "ltd",
        "limited",
        "co",
        "lp",
        "llp",
        "plc",
        "pllc",
        "gmbh",
        "ag",
        "sa",
        "bv",
        "srl",
        "technologies",
        "technology",
    }
)


def normalize(value: str | None) -> str:
    """Lowercase, collapse non-alphanumeric to spaces, strip.

    Args:
        value: String to normalize.

    Returns:
        Normalized lowercase string.
    """
    if not value:
        return ""
    return _NORM_RE.sub(" ", value.lower()).strip()


def normalize_company(value: str | None) -> str:
    """Normalize a company name, iteratively stripping corporate suffixes.

    Args:
        value: Company name to normalize.

    Returns:
        Normalized company slug with suffixes removed.
    """
    base = normalize(value)
    if not base:
        return base
    tokens = base.split()
    while len(tokens) > 1 and tokens[-1] in _CORP_SUFFIXES:
        tokens.pop()
    return " ".join(tokens)


__all__ = [
    "normalize",
    "normalize_company",
]
