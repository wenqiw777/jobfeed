"""Shared domain type aliases for cross-module contracts."""

from __future__ import annotations

from typing import Literal

StageName = Literal["stage_a", "stage_b"]
Severity = Literal["critical", "major", "minor"]

VALID_SEVERITIES: frozenset[str] = frozenset({"critical", "major", "minor"})

__all__ = ["VALID_SEVERITIES", "Severity", "StageName"]
