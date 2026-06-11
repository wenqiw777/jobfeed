"""Markdown digest rendering for evaluated job results."""

from __future__ import annotations

from datetime import UTC, datetime

from jobfeed.domain.models import (
    AttentionItem,
    AttentionReport,
    GapItem,
    JobEvaluation,
    MatchItem,
    Verdict,
    WorkflowAttention,
    WorkflowAttentionItem,
)

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
    top: int | None = None,
) -> str:
    """Render evaluated jobs into a daily Markdown digest.

    Args:
        evaluations: Evaluated jobs with optional Stage A and Stage B results.
        stats: Summary counters to include at the end of the digest.
        cutoff_at: Optional boundary for splitting apply-tier jobs into new and
            previously seen groups.
        top: Optional per-group row cap; group headers show ``(shown/total)``.

    Returns:
        Markdown digest ready for CLI or notification output.

    Raises:
        ValueError: If cutoff_at is timezone-naive or top is below 1.
    """
    _validate_cutoff(cutoff_at)
    if top is not None and top < 1:
        raise ValueError("top must be >= 1")
    apply_jobs, consider_jobs, skip_jobs = _group_evaluations(evaluations)
    lines = [
        "# Daily Digest",
        "",
        f"Date: {_digest_date()}",
        "",
        _group_header("Apply", len(apply_jobs), top),
    ]
    lines.extend(_render_apply_section(_cap(apply_jobs, top), cutoff_at))
    lines.extend(["", _group_header("Consider", len(consider_jobs), top)])
    lines.extend(_render_consider_section(_cap(consider_jobs, top)))
    lines.extend(["", "## Skip", f"({len(skip_jobs)} jobs skipped)"])
    lines.extend(["", "## Stats"])
    lines.extend(_render_stats(stats))
    return "\n".join(lines).rstrip() + "\n"


def _group_header(name: str, total: int, top: int | None) -> str:
    if top is None:
        return f"## {name}"
    return f"## {name} ({min(top, total)}/{total})"


def _cap(
    evaluations: list[JobEvaluation],
    top: int | None,
) -> list[JobEvaluation]:
    return evaluations if top is None else evaluations[:top]


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


def render_attention_footer(
    attention: WorkflowAttention,
    report: AttentionReport,
) -> str:
    """Render workflow and pipeline attention buckets as a digest footer.

    Args:
        attention: Three-bucket workflow attention report.
        report: Pipeline health attention report.

    Returns:
        Markdown footer section, or an empty string when every bucket is empty.
    """
    sections: list[str] = []
    _add_bucket(sections, "Follow-up due", _workflow_lines(attention.follow_up_today))
    _add_bucket(sections, "Interview prep", _workflow_lines(attention.interview_prep))
    _add_bucket(sections, "Going ghosted", _workflow_lines(attention.going_ghosted))
    _add_bucket(sections, "Enrich errors", _report_lines(report.enrich_errors))
    _add_bucket(
        sections, "Low quality scored", _report_lines(report.low_quality_scored)
    )
    _add_bucket(sections, "Stuck scoring", _report_lines(report.stuck_scoring))
    if not sections:
        return ""
    return "\n".join(["## Attention", *sections]).rstrip() + "\n"


def _add_bucket(sections: list[str], title: str, lines: list[str]) -> None:
    if not lines:
        return
    sections.extend(["", f"### {title} ({len(lines)})", *lines])


def _workflow_lines(items: list[WorkflowAttentionItem]) -> list[str]:
    return [
        f"- [{item.job_id}] {item.title} @ {item.company}: {item.reason}"
        for item in items
    ]


def _report_lines(items: list[AttentionItem]) -> list[str]:
    return [
        f"- [{item.job_id}] {item.title} @ {item.company}: {item.detail}"
        for item in items
    ]


__all__ = ["render_attention_footer", "render_digest"]
