"""Local-only endpoints for first-run and ongoing GUI configuration."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from jobfeed.config_editor import ConfigurationEditor, EditableConfiguration
from jobfeed.web.deps import get_configuration_editor
from jobfeed.web.schemas.configuration import (
    ConfigurationResponse,
    configuration_response,
)

router = APIRouter()


@router.get("/config")
async def get_configuration(
    editor: Annotated[ConfigurationEditor, Depends(get_configuration_editor)],
) -> ConfigurationResponse:
    """Return effective editable settings and onboarding state.

    Args:
        editor: Project-local configuration editor.

    Returns:
        Effective user-editable settings without database or secret values.
    """
    return configuration_response(editor.editable, configured=editor.is_configured)


@router.put("/config")
async def put_configuration(
    body: EditableConfiguration,
    editor: Annotated[ConfigurationEditor, Depends(get_configuration_editor)],
) -> ConfigurationResponse:
    """Atomically save valid settings and apply them to new work.

    Args:
        body: Complete GUI-managed configuration.
        editor: Project-local configuration editor.

    Returns:
        Saved effective settings with onboarding marked complete.
    """
    saved = editor.save(body)
    return configuration_response(
        EditableConfiguration.from_settings(saved), configured=True
    )


__all__ = ["router"]
