"""Deterministic search suggestions and resumable onboarding selection."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from pathlib import Path
from typing import Literal
from urllib.parse import urlencode, urlparse

from pydantic import BaseModel, ConfigDict, Field, model_validator

from jobfeed.onboarding_resume_files import _write_private_bytes
from jobfeed.onboarding_resume_types import JobProfile, ResumeDraftState

SearchSource = Literal["linkedin_guest", "indeed"]
_MAX_SUGGESTED_SEARCH_PAIRS = 6
_SEARCH_COUNTRY = "United States"
_SEARCH_GENERATOR_VERSION = "v6-six-us-title-pairs-user-selected"


class SearchSuggestion(BaseModel):
    """One editable source/query/location/URL selection."""

    model_config = ConfigDict(extra="forbid")

    id: str
    source: SearchSource
    query: str
    location: str
    url: str
    enabled: bool = True

    @model_validator(mode="after")
    def _validate_fields(self) -> SearchSuggestion:
        if not self.id.strip() or not self.query.strip() or not self.location.strip():
            raise ValueError("Search id, query, and location must not be blank")
        parsed = urlparse(self.url)
        if self.source == "linkedin_guest":
            if parsed.scheme != "https" or parsed.hostname != "www.linkedin.com":
                raise ValueError(
                    "LinkedIn search URL must use https://www.linkedin.com"
                )
            if parsed.path.rstrip("/") != "/jobs/search":
                raise ValueError("LinkedIn search URL must use the jobs search path")
        elif parsed.scheme != "https" or parsed.hostname not in {
            "indeed.com",
            "www.indeed.com",
        }:
            raise ValueError("Indeed search URL must use https://www.indeed.com")
        elif parsed.path.rstrip("/") != "/jobs":
            raise ValueError("Indeed search URL must use the jobs search path")
        return self


class SearchDraftState(BaseModel):
    """Profile-bound local search selection."""

    model_config = ConfigDict(extra="forbid")

    profile_fingerprint: str | None = None
    searches: list[SearchSuggestion] = Field(default_factory=list)


class OnboardingSearchStore:
    """Persist selected onboarding searches as a private local draft."""

    def __init__(self, path: Path) -> None:
        self._path = path.resolve()

    def load(self) -> SearchDraftState:
        """Return a validated draft or an empty state.

        Returns:
            Persisted search draft, or a fresh empty state.
        """
        if not self._path.exists():
            return SearchDraftState()
        return SearchDraftState.model_validate_json(
            self._path.read_text(encoding="utf-8")
        )

    def for_profile(self, profile: JobProfile) -> SearchDraftState:
        """Reuse selections for the same profile or generate fresh suggestions.

        Args:
            profile: Confirmed job-search profile.

        Returns:
            Search draft bound to the supplied profile.
        """
        fingerprint = _profile_fingerprint(profile)
        current = self.load()
        if current.profile_fingerprint == fingerprint:
            return current
        return self.save(generate_search_suggestions(profile), profile)

    def save(
        self,
        searches: list[SearchSuggestion],
        profile: JobProfile,
    ) -> SearchDraftState:
        """Validate and persist the complete editable selection.

        Args:
            searches: Complete user-edited search selection.
            profile: Confirmed profile owning the selection.

        Returns:
            Newly persisted search draft.
        """
        state = SearchDraftState(
            profile_fingerprint=_profile_fingerprint(profile),
            searches=searches,
        )
        _write_private_bytes(
            self._path,
            (state.model_dump_json(indent=2) + "\n").encode(),
        )
        return state


class OnboardingSearchService:
    """Bind search suggestions to the explicitly confirmed job profile."""

    def __init__(
        self,
        *,
        store: OnboardingSearchStore,
        resume_state: Callable[[], ResumeDraftState],
    ) -> None:
        self._store = store
        self._resume_state = resume_state

    def state(self) -> SearchDraftState:
        """Return selections for the current confirmed profile.

        Returns:
            Resumable search draft for the confirmed profile.
        """
        return self._store.for_profile(self._confirmed_profile())

    def save(self, searches: list[SearchSuggestion]) -> SearchDraftState:
        """Persist edited, added, and deselected searches.

        Args:
            searches: Complete edited search selection.

        Returns:
            Newly persisted search draft.
        """
        return self._store.save(searches, self._confirmed_profile())

    def _confirmed_profile(self) -> JobProfile:
        state = self._resume_state()
        if not state.is_confirmed or state.profile is None:
            raise ValueError("Confirm the job profile before selecting searches")
        return state.profile


def generate_search_suggestions(profile: JobProfile) -> list[SearchSuggestion]:
    """Generate stable LinkedIn Guest and Indeed searches from confirmed fields.

    Args:
        profile: Confirmed profile containing desired job titles.

    Returns:
        Paired, initially disabled searches for each retained title.

    Raises:
        ValueError: If the confirmed profile contains no desired title.
    """
    if not profile.desired_titles:
        raise ValueError("Confirmed profile needs at least one title")
    suggestions: list[SearchSuggestion] = []
    for title in profile.desired_titles[:_MAX_SUGGESTED_SEARCH_PAIRS]:
        suggestions.extend(
            _source_suggestions(
                title,
                _SEARCH_COUNTRY,
                profile.maximum_posting_age_days,
                enabled=False,
            )
        )
    return suggestions


def _source_suggestions(
    query: str,
    location: str,
    age_days: int,
    *,
    enabled: bool,
) -> list[SearchSuggestion]:
    seconds = age_days * 24 * 60 * 60
    linkedin = "https://www.linkedin.com/jobs/search/?" + urlencode(
        {"keywords": query, "location": location, "f_TPR": f"r{seconds}"}
    )
    indeed = "https://www.indeed.com/jobs?" + urlencode(
        {"q": query, "l": location, "fromage": age_days}
    )
    return [
        SearchSuggestion(
            id=_search_id("linkedin_guest", query, location),
            source="linkedin_guest",
            query=query,
            location=location,
            url=linkedin,
            enabled=enabled,
        ),
        SearchSuggestion(
            id=_search_id("indeed", query, location),
            source="indeed",
            query=query,
            location=location,
            url=indeed,
            enabled=enabled,
        ),
    ]


def _search_id(source: SearchSource, query: str, location: str) -> str:
    value = f"{source}\0{query}\0{location}".encode()
    return hashlib.sha256(value).hexdigest()[:16]


def _profile_fingerprint(profile: JobProfile) -> str:
    payload = f"{_SEARCH_GENERATOR_VERSION}\0{profile.model_dump_json()}"
    return hashlib.sha256(payload.encode()).hexdigest()


__all__ = [
    "OnboardingSearchService",
    "OnboardingSearchStore",
    "SearchDraftState",
    "SearchSuggestion",
    "generate_search_suggestions",
]
