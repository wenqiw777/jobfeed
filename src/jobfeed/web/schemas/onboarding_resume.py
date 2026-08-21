"""Wire schemas for résumé upload and editable onboarding profiles."""

from __future__ import annotations

from pydantic import BaseModel

from jobfeed.onboarding_resume_types import JobProfile, ResumeDraftState


class ResumeStateResponse(BaseModel):
    """Resumable résumé preview and profile without local filesystem paths."""

    original_name: str | None = None
    extracted_text: str | None = None
    profile: JobProfile | None = None
    is_confirmed: bool = False


def resume_state_response(state: ResumeDraftState) -> ResumeStateResponse:
    """Convert an internal draft to the path-free browser response.

    Args:
        state: Internal résumé draft with store-only metadata.

    Returns:
        Browser-safe response without stored path or basename.
    """
    return ResumeStateResponse(
        original_name=state.original_name,
        extracted_text=state.extracted_text,
        profile=state.profile,
        is_confirmed=state.is_confirmed,
    )


__all__ = ["ResumeStateResponse", "resume_state_response"]
