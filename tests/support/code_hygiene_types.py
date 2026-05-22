"""Shared value objects for code hygiene checks."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class HygieneViolation:
    """A single code hygiene violation with source location."""

    path: Path
    line: int
    message: str

    def format(self) -> str:
        """Format the violation for assertion output.

        Returns:
            Human-readable violation string.
        """
        return f"{self.path}:{self.line}: {self.message}"
