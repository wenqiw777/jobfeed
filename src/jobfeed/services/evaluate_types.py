"""Typed containers used to configure EvaluateService."""

from __future__ import annotations

from dataclasses import dataclass

from jobfeed.domain.filtering import HardFilters
from jobfeed.ports.llm import LLMClient
from jobfeed.ports.ml_gate import MLGate
from jobfeed.ports.prompts import PromptRenderer
from jobfeed.ports.store import JobStore
from jobfeed.ports.store_ops import StoreOpsMixin
from jobfeed.ports.store_status import StoreStatusMixin


@dataclass(frozen=True, kw_only=True)
class EvaluateDependencies:
    """Ports used by EvaluateService."""

    store: JobStore
    store_ops: StoreOpsMixin
    store_status: StoreStatusMixin
    prompt_renderer: PromptRenderer
    llm_stage_a: LLMClient
    llm_stage_b: LLMClient
    llm_stage_b_sweep: LLMClient | None = None
    ml_gate: MLGate | None = None
    hard_filters: HardFilters | None = None


@dataclass(frozen=True, kw_only=True)
class EvaluateLLMConfig:
    """LLM runtime settings consumed by EvaluateService."""

    stage_a: str
    stage_b: str
    max_concurrent: int
    max_daily_score_calls: int
    max_daily_cost_usd: float


@dataclass(frozen=True, kw_only=True)
class EvaluateRuntimeConfig:
    """Runtime knobs used by EvaluateService."""

    llm: EvaluateLLMConfig
    stage_a_threshold: int
    resume_text: str
    default_eval_limit: int = 150
    ml_gate_enabled: bool = False
    ml_gate_max_candidates: int = 5000
    ghost_days: int = 30
    archive_ignored_days: int = 14


__all__ = ["EvaluateDependencies", "EvaluateLLMConfig", "EvaluateRuntimeConfig"]
