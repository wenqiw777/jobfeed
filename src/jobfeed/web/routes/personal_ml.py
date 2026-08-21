"""Local API for personal relevance-learning progress."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from jobfeed.config_editor import ConfigurationEditor
from jobfeed.personal_ml_learning import PersonalMLLearningService
from jobfeed.web.deps import get_configuration_editor, get_personal_ml_service
from jobfeed.web.errors import ApiError
from jobfeed.web.schemas.configuration import (
    ConfigurationResponse,
    configuration_response,
)
from jobfeed.web.schemas.personal_ml import PersonalMLStatusResponse

router = APIRouter()


@router.get("/personal-ml/status")
async def get_personal_ml_status(
    service: Annotated[PersonalMLLearningService, Depends(get_personal_ml_service)],
    editor: Annotated[ConfigurationEditor, Depends(get_configuration_editor)],
) -> PersonalMLStatusResponse:
    """Return current teacher-label, ranking, and shadow progress.

    Args:
        service: Shared personal learning policy service.
        editor: Effective local configuration editor.

    Returns:
        Current lifecycle state and measured validation evidence.
    """
    scoring = editor.editable.scoring
    status = await service.status(
        quick_pass_threshold=scoring.stage_a_threshold,
        enabled=scoring.ml_gate_enabled,
    )
    return PersonalMLStatusResponse.from_domain(status)


@router.post("/personal-ml/activate")
async def activate_personal_ml(
    service: Annotated[PersonalMLLearningService, Depends(get_personal_ml_service)],
    editor: Annotated[ConfigurationEditor, Depends(get_configuration_editor)],
) -> ConfigurationResponse:
    """Explicitly enable only a threshold that passed future shadow checks.

    Args:
        service: Shared personal learning policy service.
        editor: Effective local configuration editor.

    Returns:
        Saved configuration with the validated filter enabled.

    Raises:
        ApiError: If shadow validation has not made the model ready.
    """
    editable = editor.editable
    status = await service.status(
        quick_pass_threshold=editable.scoring.stage_a_threshold,
        enabled=False,
    )
    if status.state != "ready" or status.model_threshold is None:
        raise ApiError(
            409,
            "personal_ml_not_ready",
            "The personal job filter has not passed shadow validation yet",
        )
    updated = editable.model_copy(
        update={
            "scoring": editable.scoring.model_copy(update={"ml_gate_enabled": True}),
            "ml_gate": editable.ml_gate.model_copy(
                update={"threshold_override": status.model_threshold}
            ),
        }
    )
    saved = editor.save(updated)
    return configuration_response(
        updated.from_settings(saved),
        configured=True,
    )


__all__ = ["router"]
