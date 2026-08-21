"""Validated draft types for résumé onboarding and editable job profiles."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

WorkMode = Literal["remote", "hybrid", "on-site"]


class JobProfile(BaseModel):
    """Structured AI suggestion that the user must edit or confirm."""

    model_config = ConfigDict(extra="forbid")

    desired_titles: list[str]
    seniority_levels: list[str]
    target_countries: list[str]
    target_locations: list[str]
    work_modes: list[WorkMode]
    industries: list[str]
    company_sizes: list[str]
    work_authorization: str
    hiring_timeline: str
    excluded_titles: list[str]
    excluded_companies: list[str]
    excluded_locations: list[str]
    excluded_keywords: list[str]
    maximum_posting_age_days: int = Field(ge=1, le=365)
    resume_evidence: list[str]

    @field_validator(
        "desired_titles",
        "seniority_levels",
        "target_countries",
        "target_locations",
        "industries",
        "company_sizes",
        "excluded_titles",
        "excluded_companies",
        "excluded_locations",
        "excluded_keywords",
        "resume_evidence",
    )
    @classmethod
    def _clean_list(cls, values: list[str]) -> list[str]:
        return [value.strip() for value in values if value.strip()]


class ResumeDraftState(BaseModel):
    """Secret-free, resumable résumé and job-profile draft."""

    model_config = ConfigDict(extra="forbid")

    stored_name: str | None = None
    original_name: str | None = None
    extracted_text: str | None = None
    profile: JobProfile | None = None
    is_confirmed: bool = False


@dataclass(frozen=True, kw_only=True)
class StoredResume:
    """One validated original résumé and its local text extraction."""

    path: Path
    stored_name: str
    original_name: str
    extracted_text: str


__all__ = [
    "JobProfile",
    "ResumeDraftState",
    "StoredResume",
    "WorkMode",
]
