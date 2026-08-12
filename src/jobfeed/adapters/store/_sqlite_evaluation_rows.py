"""Hydrate SQLite jobs/evaluations join rows into domain results."""

from __future__ import annotations

from typing import Any, cast

import aiosqlite

from jobfeed.adapters.store._sqlite_values import _job_from_row, _optional_json
from jobfeed.domain.models import (
    FitAnalysis,
    GapItem,
    JobEvaluation,
    MatchItem,
    StageAResult,
    StageBResult,
    Verdict,
)
from jobfeed.domain.types import VALID_SEVERITIES, Severity


def _evaluation_from_row(row: aiosqlite.Row) -> JobEvaluation:
    """Hydrate a joined job/evaluation row with defensive legacy semantics."""
    return JobEvaluation(
        job=_job_from_row(row),
        stage_a=_stage_a(row),
        stage_b=_stage_b(row),
        stage_b_status=row["stage_b_status"],
        stage_b_blocks=_stage_b_display_blocks(row),
    )


def _stage_a(row: aiosqlite.Row) -> StageAResult | None:
    if row["stage_a_status"] != "completed":
        return None
    return StageAResult(
        score=row["stage_a_score"],
        one_line=row["stage_a_one_line"],
        timing_eligible=row["stage_a_timing_eligible"],
        model=row["stage_a_model"],
        prompt_hash=row["stage_a_prompt_hash"],
        resume_hash=row["stage_a_resume_hash"],
        cost_usd=row["stage_a_cost_usd"],
    )


def _stage_b(row: aiosqlite.Row) -> StageBResult | None:
    if row["stage_b_status"] != "completed" or row["stage_b_verdict"] is None:
        return None
    fit = _optional_json(row["stage_b_fit_json"])
    if not isinstance(fit, dict) or fit.get("score_0_100") is None:
        return None
    hooks = _optional_json(row["stage_b_hooks_json"])
    raw_blocks = {
        "verdict": _optional_json(row["stage_b_verdict_json"]),
        "jd_summary": _optional_json(row["stage_b_summary_json"]),
        "fit_analysis": fit,
        "resume_hooks": hooks,
    }
    return StageBResult(
        verdict=Verdict(row["stage_b_verdict"]),
        jd_summary=row["stage_b_jd_summary"],
        fit_analysis=FitAnalysis(
            score=fit["score_0_100"],
            strengths=_strengths(fit),
            gaps=_gaps(fit),
        ),
        resume_hooks=_hooks(hooks),
        model=row["stage_b_model"],
        prompt_hash=row["stage_b_prompt_hash"],
        resume_hash=row["stage_b_resume_hash"],
        cost_usd=row["stage_b_cost_usd"],
        raw_blocks=raw_blocks,
    )


def _strengths(fit: dict[str, Any]) -> list[MatchItem]:
    values = fit.get("strong_match")
    if not isinstance(values, list):
        return []
    return [
        MatchItem(
            requirement=item["requirement"],
            evidence=item.get("evidence_from_resume", item.get("evidence", "")),
        )
        for item in values
    ]


def _gaps(fit: dict[str, Any]) -> list[GapItem]:
    values = fit.get("gaps")
    if not isinstance(values, list):
        return []
    return [
        GapItem(
            requirement=item["requirement"],
            severity=_severity(item["severity"]),
            mitigation=item.get("mitigation") or "",
        )
        for item in values
    ]


def _severity(value: str) -> Severity:
    mapped = {"blocker": "critical", "notable": "major"}.get(value, value)
    if mapped not in VALID_SEVERITIES:
        raise ValueError(f"invalid gap severity: {value}")
    return cast(Severity, mapped)


def _hooks(value: object) -> list[str]:
    if not isinstance(value, dict):
        return []
    if "lead_with" in value:
        result = [str(value["lead_with"])] if value["lead_with"] else []
        supporting = value.get("supporting")
        if isinstance(supporting, list):
            result.extend(str(item) for item in supporting)
        return result
    hooks = value.get("hooks")
    return [str(item) for item in hooks] if isinstance(hooks, list) else []


def _stage_b_display_blocks(row: aiosqlite.Row) -> dict[str, object] | None:
    if row["stage_b_status"] != "completed":
        return None
    fit = _optional_json(row["stage_b_fit_json"])
    if not isinstance(fit, dict):
        return None
    score = fit.get("score_0_100")
    return {
        "jd_summary": row["stage_b_jd_summary"] or "",
        "fit_score": score
        if isinstance(score, int) and not isinstance(score, bool)
        else None,
        "strengths": [
            {"requirement": item.requirement, "evidence": item.evidence}
            for item in _strengths(fit)
        ],
        "gaps": [
            {
                "requirement": item.requirement,
                "severity": item.severity,
                "mitigation": item.mitigation,
            }
            for item in _gaps(fit)
        ],
        "hooks": _optional_json(row["stage_b_hooks_json"]),
    }
