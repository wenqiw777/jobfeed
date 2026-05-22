"""Markdown digest rendering for evaluated job results."""

from __future__ import annotations

from datetime import UTC, datetime

from jobfeed.domain.models import GapItem, JobEvaluation, MatchItem, Verdict

STAT_KEYS = [
    "total_jobs",
    "scraped_today",
    "llm_calls_today",
    "stage_b_evaluated",
    "filtered_count",
]


def render_digest(
    evaluations: list[JobEvaluation],
    stats: dict[str, object],
    cutoff_at: datetime | None = None,
) -> str:
    """Render evaluated jobs into a daily Markdown digest.

    Args:
        evaluations: Evaluated jobs with optional Stage A and Stage B results.
        stats: Summary counters to include at the end of the digest.
        cutoff_at: Optional boundary for splitting apply-tier jobs into new and
            previously seen groups.

    Returns:
        Markdown digest ready for CLI or notification output.

    Raises:
        ValueError: If cutoff_at is timezone-naive.
    """
    _validate_cutoff(cutoff_at)
    apply_jobs, consider_jobs, skip_jobs = _group_evaluations(evaluations)
    lines = [
        "# Daily Digest",
        "",
        f"Date: {_digest_date()}",
        "",
        "## Apply",
    ]
    lines.extend(_render_apply_section(apply_jobs, cutoff_at))
    lines.extend(["", "## Consider"])
    lines.extend(_render_consider_section(consider_jobs))
    lines.extend(["", "## Skip", f"({len(skip_jobs)} jobs skipped)"])
    lines.extend(["", "## Stats"])
    lines.extend(_render_stats(stats))
    return "\n".join(lines).rstrip() + "\n"


def _group_evaluations(
    evaluations: list[JobEvaluation],
) -> tuple[list[JobEvaluation], list[JobEvaluation], list[JobEvaluation]]:
    apply_jobs: list[JobEvaluation] = []
    consider_jobs: list[JobEvaluation] = []
    skip_jobs: list[JobEvaluation] = []
    for evaluation in evaluations:
        if evaluation.stage_b is None:
            skip_jobs.append(evaluation)
        elif evaluation.stage_b.verdict == Verdict.APPLY:
            apply_jobs.append(evaluation)
        elif evaluation.stage_b.verdict == Verdict.CONSIDER:
            consider_jobs.append(evaluation)
        else:
            skip_jobs.append(evaluation)
    return apply_jobs, consider_jobs, skip_jobs


def _render_apply_section(
    evaluations: list[JobEvaluation],
    cutoff_at: datetime | None,
) -> list[str]:
    if not evaluations:
        return ["(none)"]
    if cutoff_at is None:
        return _render_apply_jobs(evaluations)
    new_jobs = [item for item in evaluations if _discovered_at(item) > cutoff_at]
    seen_jobs = [item for item in evaluations if _discovered_at(item) <= cutoff_at]
    lines = ["### New"]
    lines.extend(_render_apply_jobs(new_jobs))
    lines.extend(["", "### Previously seen"])
    lines.extend(_render_apply_jobs(seen_jobs))
    return lines


def _validate_cutoff(cutoff_at: datetime | None) -> None:
    if cutoff_at is None:
        return
    if cutoff_at.tzinfo is None or cutoff_at.utcoffset() is None:
        raise ValueError("cutoff_at must be timezone-aware")


def _discovered_at(evaluation: JobEvaluation) -> datetime:
    discovered_at = evaluation.job.discovered_at
    if discovered_at.tzinfo is None or discovered_at.utcoffset() is None:
        raise ValueError("job.discovered_at must be timezone-aware")
    return discovered_at


def _render_apply_jobs(evaluations: list[JobEvaluation]) -> list[str]:
    if not evaluations:
        return ["(none)"]
    lines: list[str] = []
    for evaluation in evaluations:
        stage_b = evaluation.stage_b
        if stage_b is None:
            continue
        lines.extend(
            [
                f"- **{stage_b.fit_analysis.score}** "
                f"{evaluation.job.title} @ {evaluation.job.company}",
                f"  - Summary: {stage_b.jd_summary}",
                f"  - Strengths: {_format_strengths(stage_b.fit_analysis.strengths)}",
                f"  - Gaps: {_format_gaps(stage_b.fit_analysis.gaps)}",
                f"  - URL: {evaluation.job.url}",
            ]
        )
    return lines


def _render_consider_section(evaluations: list[JobEvaluation]) -> list[str]:
    if not evaluations:
        return ["(none)"]
    lines: list[str] = []
    for evaluation in evaluations:
        stage_b = evaluation.stage_b
        if stage_b is None:
            continue
        one_line = (
            evaluation.stage_a.one_line
            if evaluation.stage_a is not None
            else stage_b.jd_summary
        )
        lines.append(
            f"- **{stage_b.fit_analysis.score}** "
            f"{evaluation.job.title} @ {evaluation.job.company}: {one_line}"
        )
    return lines


def _render_stats(stats: dict[str, object]) -> list[str]:
    return [f"- {key}: {stats.get(key, 0)}" for key in STAT_KEYS]


def _format_strengths(strengths: list[MatchItem]) -> str:
    if not strengths:
        return "(none)"
    return "; ".join(f"{item.requirement} ({item.evidence})" for item in strengths)


def _format_gaps(gaps: list[GapItem]) -> str:
    if not gaps:
        return "(none)"
    return "; ".join(
        f"{item.requirement} [{item.severity}] {item.mitigation}" for item in gaps
    )


def _digest_date() -> str:
    return datetime.now(UTC).date().isoformat()


__all__ = ["render_digest"]
