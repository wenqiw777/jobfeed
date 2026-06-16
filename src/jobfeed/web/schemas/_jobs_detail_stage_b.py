"""Stage B detail DTOs + mapping for ``GET /api/jobs/{id}``.

Split out of ``jobs_detail.py`` to keep that module under the file-length
gate. Holds the Stage B response blocks (verdict, JD summary, fit analysis,
resume hooks) and the verdict-bearing / verdict-independent mappers.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from jobfeed.domain.models import JobEvaluation, StageBResult


class StrengthDetail(BaseModel):
    """One matched requirement with its resume evidence."""

    requirement: str
    evidence: str


class GapDetail(BaseModel):
    """One missing/weak requirement with severity and mitigation."""

    requirement: str
    severity: str
    mitigation: str


class ResumeHooksDetail(BaseModel):
    """The three resume-hook blocks of the Stage B output."""

    lead_with: str
    supporting: list[str]
    avoid_mentioning: list[str]


class StageBDetail(BaseModel):
    """Stage B blocks: verdict, JD summary, fit analysis, resume hooks.

    Display DTO — it must never 500 on real-data nulls. Stage B rows in the
    live corpus can carry a NULL/absent JD summary or fit score (the verdict-
    independent fallback also fills here), so ``jd_summary`` and ``fit_score``
    are nullable and the list fields default to empty. ``verdict`` stays a
    plain string ("" when unscored, which the pill reads). ``hooks`` keeps a
    non-optional empty default so both mapper paths and the frontend never
    have to special-case its absence.
    """

    verdict: str
    jd_summary: str | None = None
    fit_score: int | None = None
    strengths: list[StrengthDetail] = Field(default_factory=list)
    gaps: list[GapDetail] = Field(default_factory=list)
    hooks: ResumeHooksDetail = Field(
        default_factory=lambda: ResumeHooksDetail(
            lead_with="", supporting=[], avoid_mentioning=[]
        )
    )


def stage_b_section(evaluation: JobEvaluation | None) -> StageBDetail | None:
    """Build the detail's Stage B section, fit-score-first.

    Prefers the verdict-bearing ``stage_b`` result; otherwise falls back to
    the verdict-independent ``stage_b_blocks`` (the same fit source the list
    row reads) so a completed row whose flat verdict is NULL still shows its
    FIT number and Stage B blocks. The blocks dict is already keyed to
    ``StageBDetail`` (and its nested item/hook models), so pydantic validates
    it directly; the verdict is forced empty so the pill reads "unscored".

    Args:
        evaluation: The job's evaluation aggregate, or None when absent.

    Returns:
        The Stage B detail section, or None when there is no Stage B content.
    """
    if evaluation is None:
        return None
    if evaluation.stage_b is not None:
        return _stage_b_detail(evaluation.stage_b)
    blocks = evaluation.stage_b_blocks
    if blocks is not None:
        return StageBDetail.model_validate({**blocks, "verdict": ""})
    return None


def _stage_b_detail(stage_b: StageBResult) -> StageBDetail:
    """Map a Stage B result to its DTO, lifting the three hook blocks."""
    fit = stage_b.fit_analysis
    return StageBDetail(
        verdict=stage_b.verdict.value,
        jd_summary=stage_b.jd_summary,
        fit_score=fit.score,
        strengths=[
            StrengthDetail(requirement=item.requirement, evidence=item.evidence)
            for item in fit.strengths
        ],
        gaps=[
            GapDetail(
                requirement=item.requirement,
                severity=item.severity,
                mitigation=item.mitigation,
            )
            for item in fit.gaps
        ],
        hooks=_resume_hooks_detail(stage_b),
    )


def _resume_hooks_detail(stage_b: StageBResult) -> ResumeHooksDetail:
    """Read the lead_with/supporting/avoid_mentioning hook blocks.

    Prefers the persisted ``raw_blocks['resume_hooks']`` object; when it is
    absent or malformed, falls back to the normalized ``resume_hooks`` list
    (first item leads, the rest support — mirroring the store's derived
    block) with no avoid-mentioning entries.
    """
    blocks = stage_b.raw_blocks or {}
    hooks_block = blocks.get("resume_hooks")
    if isinstance(hooks_block, dict) and "lead_with" in hooks_block:
        return ResumeHooksDetail(
            lead_with=str(hooks_block.get("lead_with") or ""),
            supporting=_string_list(hooks_block.get("supporting")),
            avoid_mentioning=_string_list(hooks_block.get("avoid_mentioning")),
        )
    flat = stage_b.resume_hooks
    return ResumeHooksDetail(
        lead_with=flat[0] if flat else "",
        supporting=list(flat[1:]),
        avoid_mentioning=[],
    )


def _string_list(value: object) -> list[str]:
    """Coerce a raw JSON value to a list of strings (non-lists -> empty)."""
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


__all__ = [
    "GapDetail",
    "ResumeHooksDetail",
    "StageBDetail",
    "StrengthDetail",
    "stage_b_section",
]
