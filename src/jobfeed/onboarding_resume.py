"""Local résumé upload, extraction, draft persistence, and profile workflow."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from pydantic import ValidationError

from jobfeed.onboarding_resume_files import ResumeFileStore, _write_private_bytes
from jobfeed.onboarding_resume_types import (
    JobProfile,
    ResumeDraftState,
    StoredResume,
)
from jobfeed.onboarding_types import ProviderName, ProviderOnboardingState

_MIN_FENCED_LINES = 3


class ProfileAnalyzer(Protocol):
    """Capability that turns résumé text into a structured suggestion."""

    async def analyze(
        self,
        provider: ProviderName,
        model: str,
        resume_text: str,
    ) -> JobProfile:
        """Return a complete structured profile suggestion.

        Args:
            provider: Verified provider selected during onboarding.
            model: Selected Detailed model identifier.
            resume_text: Locally extracted résumé text.

        Returns:
            Complete structured suggestion for user review.
        """
        ...


class ResumeDraftStore:
    """Persist secret-free résumé preview and editable profile draft state."""

    def __init__(self, path: Path) -> None:
        """Create a draft store at ``data/onboarding-resume.json``."""
        self._path = path.resolve()

    def load(self) -> ResumeDraftState:
        """Return the current draft or an empty state when absent.

        Returns:
            Validated résumé and profile draft state.
        """
        if not self._path.exists():
            return ResumeDraftState()
        return ResumeDraftState.model_validate_json(
            self._path.read_text(encoding="utf-8")
        )

    def save_upload(self, uploaded: StoredResume) -> ResumeDraftState:
        """Replace the résumé preview and invalidate its prior analysis.

        Args:
            uploaded: Validated stored résumé and extracted text.

        Returns:
            Persisted draft containing the new preview.
        """
        state = ResumeDraftState(
            stored_name=uploaded.stored_name,
            original_name=uploaded.original_name,
            extracted_text=uploaded.extracted_text,
        )
        self._write(state)
        return state

    def save_profile(
        self, profile: JobProfile, *, is_confirmed: bool
    ) -> ResumeDraftState:
        """Persist an AI suggestion or the user's confirmed edits.

        Args:
            profile: Complete structured profile.
            is_confirmed: Whether the values were explicitly user-confirmed.

        Returns:
            Updated persisted draft.

        Raises:
            ValueError: If no résumé has been uploaded.
        """
        current = self.load()
        if current.extracted_text is None:
            raise ValueError("Upload a résumé before saving a job profile")
        state = current.model_copy(
            update={"profile": profile, "is_confirmed": is_confirmed}
        )
        self._write(state)
        return state

    def _write(self, state: ResumeDraftState) -> None:
        content = state.model_dump_json(indent=2) + "\n"
        _write_private_bytes(self._path, content.encode())


class ResumeOnboardingService:
    """Coordinate upload, provider analysis, and explicit profile confirmation."""

    def __init__(
        self,
        *,
        files: ResumeFileStore,
        drafts: ResumeDraftStore,
        analyzer: ProfileAnalyzer,
        provider_state: Callable[[], ProviderOnboardingState],
    ) -> None:
        """Create the résumé workflow from injected persistence and AI boundaries."""
        self._files = files
        self._drafts = drafts
        self._analyzer = analyzer
        self._provider_state = provider_state

    def state(self) -> ResumeDraftState:
        """Return resumable résumé and profile draft state.

        Returns:
            Current secret-free résumé workflow state.
        """
        return self._drafts.load()

    def upload(self, filename: str, content: bytes) -> ResumeDraftState:
        """Validate a replacement résumé and persist its extracted preview.

        Args:
            filename: Browser-supplied original filename.
            content: Uploaded file bytes.

        Returns:
            Updated draft with extracted text.
        """
        previous = self._drafts.load()
        uploaded = self._files.save(filename, content)
        state = self._drafts.save_upload(uploaded)
        if previous.stored_name and previous.stored_name != uploaded.stored_name:
            self._files.delete(previous.stored_name)
        return state

    async def analyze(self) -> ResumeDraftState:
        """Analyze the current résumé with the selected Detailed model.

        Returns:
            Draft containing the provider's validated profile suggestion.

        Raises:
            ValueError: If résumé or provider setup is incomplete.
        """
        current = self._drafts.load()
        if current.extracted_text is None:
            raise ValueError("Upload a résumé before running analysis")
        provider = self._provider_state()
        if (
            not provider.connected
            or provider.provider is None
            or provider.detailed_model is None
        ):
            raise ValueError("Complete provider setup before analyzing the résumé")
        profile = await self._analyzer.analyze(
            provider.provider,
            provider.detailed_model,
            current.extracted_text,
        )
        return self._drafts.save_profile(profile, is_confirmed=False)

    def confirm(self, profile: JobProfile) -> ResumeDraftState:
        """Persist the user's edited profile as explicitly confirmed.

        Args:
            profile: Complete user-reviewed profile.

        Returns:
            Draft marked explicitly confirmed.
        """
        return self._drafts.save_profile(profile, is_confirmed=True)


def parse_job_profile(content: str) -> JobProfile:
    """Parse a model's JSON-only response into the frozen profile schema.

    Args:
        content: Raw model response, optionally enclosed in a Markdown fence.

    Returns:
        Fully validated structured profile.

    Raises:
        ValueError: If JSON or required profile fields are invalid.
    """
    candidate = _strip_json_fence(content)
    try:
        payload = json.loads(candidate)
        return JobProfile.model_validate(payload)
    except (json.JSONDecodeError, ValidationError, TypeError) as exc:
        raise ValueError("The provider did not return a valid job profile") from exc


def _strip_json_fence(content: str) -> str:
    stripped = content.strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    if len(lines) >= _MIN_FENCED_LINES and lines[-1].strip() == "```":
        return "\n".join(lines[1:-1]).strip()
    return stripped


__all__ = [
    "ProfileAnalyzer",
    "ResumeDraftStore",
    "ResumeFileStore",
    "ResumeOnboardingService",
    "parse_job_profile",
]
