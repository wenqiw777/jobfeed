"""Wire models for local GUI configuration."""

from __future__ import annotations

from jobfeed.config_editor import EditableConfiguration


class ConfigurationResponse(EditableConfiguration):
    """Editable settings plus first-run completion state."""

    configured: bool


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
        {**editable.model_dump(mode="json"), "configured": configured}
    )


__all__ = ["ConfigurationResponse", "configuration_response"]
