"""Performance observation domain dataclasses."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(kw_only=True)
class StepTiming:
    """Elapsed wall-clock time for one pipeline step execution."""

    run_id: str
    step_type: str
    step_name: str
    elapsed_ms: float
    is_error: bool = False
    created_at: datetime | None = None
