"""Wire models for local GUI configuration."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from jobfeed.config_editor import EditableConfiguration


class MLGatePerformance(BaseModel):
    """Headline evaluation metrics for the selected local model."""

    model_config = ConfigDict(extra="forbid")

    threshold: float = Field(ge=0, le=1)
    recall: float = Field(ge=0, le=1)
    precision: float = Field(ge=0, le=1)
    f1: float = Field(ge=0, le=1)
    irrelevant_rejection: float = Field(ge=0, le=1)
    training_jobs: int = Field(ge=1)


class ConfigurationResponse(EditableConfiguration):
    """Editable settings plus first-run completion state."""

    configured: bool
    ml_gate_performance: MLGatePerformance | None = None


def configuration_response(
    editable: EditableConfiguration, *, configured: bool
) -> ConfigurationResponse:
    """Build a configuration response from the validated editable model.

    Args:
        editable: Effective GUI-managed settings.
        configured: Whether the project config file exists.

    Returns:
        Wire response with onboarding state.
    """
    return ConfigurationResponse.model_validate(
        {
            **editable.model_dump(mode="json"),
            "configured": configured,
            "ml_gate_performance": _load_ml_gate_performance(editable),
        }
    )


def _load_ml_gate_performance(
    editable: EditableConfiguration,
) -> MLGatePerformance | None:
    """Read safe headline metrics without loading the ML toolchain."""
    version = editable.ml_gate.model_version
    meta_path = Path(editable.ml_gate.model_dir) / f"{version}.meta.json"
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        if not isinstance(meta, dict) or meta.get("version") != version:
            return None
        return MLGatePerformance.model_validate(
            {
                "threshold": meta["threshold"],
                "recall": meta["recall_pos"],
                "precision": meta["precision_pos"],
                "f1": meta["f1"],
                "irrelevant_rejection": meta["neg_blocked_pct"],
                "training_jobs": meta["train_size"],
            }
        )
    except (OSError, json.JSONDecodeError, KeyError, ValidationError):
        return None


__all__ = [
    "ConfigurationResponse",
    "MLGatePerformance",
    "configuration_response",
]
