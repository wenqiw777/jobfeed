"""Assemble evaluation policy from application settings at the wiring boundary."""

from __future__ import annotations

from jobfeed.config import Settings
from jobfeed.personal_ml_learning import PersonalMLLearningService
from jobfeed.services._evaluate_canonical import _PolicySnapshot, _snapshot_for_config
from jobfeed.services._evaluate_gate import gate_mode_for_state
from jobfeed.services.evaluate_types import EvaluateLLMConfig, EvaluateRuntimeConfig


def evaluation_runtime_config(
    settings: Settings, *, resume_text: str = "", threshold: int | None = None
) -> EvaluateRuntimeConfig:
    """Build the same immutable scoring settings for runners and readers.

    Args:
        settings: Effective application settings.
        resume_text: Loaded resume text for paid scoring.
        threshold: Optional command-specific quick-score threshold.

    Returns:
        A frozen evaluation configuration whose policy fields match the UI.
    """
    llm = settings.llm
    seniority = settings.seniority_gate
    return EvaluateRuntimeConfig(
        llm=EvaluateLLMConfig(
            stage_a=llm.stage_a,
            stage_b=llm.stage_b,
            max_concurrent=llm.max_concurrent,
            max_daily_score_calls=llm.max_daily_score_calls,
            max_daily_cost_usd=llm.max_daily_cost_usd,
        ),
        stage_a_threshold=(
            threshold if threshold is not None else settings.scoring.stage_a_threshold
        ),
        resume_text=resume_text,
        default_eval_limit=settings.scoring.default_eval_limit,
        ml_gate_enabled=settings.scoring.ml_gate_enabled,
        ml_gate_max_candidates=settings.ml_gate.max_candidates,
        seniority_gate_mode=seniority.mode,
        stage_a_prompt_version=settings.scoring.stage_a_prompt_version,
        stage_b_prompt_version=settings.scoring.stage_b_prompt_version,
        resume_version=settings.scoring.resume_version,
        ml_gate_model_version=settings.ml_gate.model_version,
        ml_gate_threshold_override=settings.ml_gate.threshold_override,
        ml_gate_policy_version=settings.ml_gate.policy_version,
        seniority_gate_model_version=seniority.model_version,
        seniority_gate_threshold=seniority.out_of_scope_threshold,
        seniority_gate_policy_version=seniority.policy_version,
    )


async def current_policy_for_settings(
    settings: Settings, personal_ml: PersonalMLLearningService
) -> _PolicySnapshot:
    """Resolve the paid policy currently configured for API score reads.

    Args:
        settings: Effective GUI or loaded file configuration.
        personal_ml: Read-only learning lifecycle service.

    Returns:
        The same explicit policy fields used by a Stage A/B paid run.
    """
    config = evaluation_runtime_config(settings)
    status = await personal_ml.status(
        quick_pass_threshold=config.stage_a_threshold,
        enabled=config.ml_gate_enabled,
    )
    mode = gate_mode_for_state(config.ml_gate_enabled, status.state)
    return _snapshot_for_config(config, mode)
