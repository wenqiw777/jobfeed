"""Click commands for read-only status queries: list and stats."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from typing import cast

import click

from jobfeed.cli import AppContext, require_app, run_with_store
from jobfeed.cli._window import parse_window_back
from jobfeed.domain.models_status import StatusFilter, StatusInfo
from jobfeed.domain.status import LIST_DEFAULT_STATUSES
from jobfeed.services.application import ApplicationService, ApplicationStore
from jobfeed.services.workflow import WorkflowStore

_COMPANY_WIDTH = 24
_TITLE_WIDTH = 40

# ── list ──────────────────────────────────────────────────────────────


@click.command(name="list", help="List jobs by status.")
@click.option(
    "--status",
    "status_filter",
    default=None,
    help="Comma-separated status values, or 'all' for every status.",
)
@click.option(
    "--days",
    "days_window",
    default=None,
    help="Status changed within: Nd (days), Nw (weeks), or since YYYY-MM-DD.",
)
@click.option(
    "--needs-followup",
    is_flag=True,
    help="Only jobs whose follow-up date is past.",
)
@click.option(
    "--no-response",
    "no_response_days",
    default=None,
    type=int,
    help="Applied/interviewing but silent for N days.",
)
@click.option(
    "--notes-contain",
    "notes_contain",
    default=None,
    help="Only jobs whose notes contain this text (case-insensitive).",
)
@click.option(
    "--limit",
    default=None,
    type=click.IntRange(min=1),
    help="Maximum rows to show.",
)
@click.option(
    "--allow-empty",
    is_flag=True,
    help="Exit 0 even when no jobs match.",
)
@click.option("--md", "markdown", is_flag=True, help="Markdown table output.")
@click.option("--json", "as_json", is_flag=True, help="JSON array output.")
@click.pass_context
def list_cmd(ctx: click.Context, /, **kwargs: object) -> None:
    """List jobs filtered by status, recency, notes, and follow-up state.

    Args:
        ctx: Click invocation context.
        kwargs: Click option values keyed by option name.
    """
    app = require_app(ctx)
    filters = _build_filter(kwargs)
    rows = asyncio.run(_run_list(app, filters))
    if not rows:
        click.echo("No matching jobs.", err=True)
        if not cast(bool, kwargs["allow_empty"]):
            ctx.exit(1)
        # --allow-empty: fall through so --json still emits [] and plain
        # prints nothing.
    if cast(bool, kwargs["as_json"]):
        _print_json(rows)
    elif cast(bool, kwargs["markdown"]):
        _print_markdown(rows)
    else:
        _print_plain(rows)


def _build_filter(opts: dict[str, object]) -> StatusFilter:
    """Translate CLI options into a store StatusFilter.

    --days passes through parse_window_back as an exact ``since`` cutoff:
    now-N for Nd/Nw, and midnight UTC for a YYYY-MM-DD date.
    """
    raw_window = cast(str | None, opts["days_window"])
    return StatusFilter(
        statuses=_parse_statuses(cast(str | None, opts["status_filter"])),
        since=parse_window_back(raw_window) if raw_window is not None else None,
        no_response_days=cast(int | None, opts["no_response_days"]),
        needs_followup=cast(bool, opts["needs_followup"]),
        notes_contain=cast(str | None, opts["notes_contain"]),
        limit=cast(int | None, opts["limit"]),
    )


def _parse_statuses(raw: str | None) -> frozenset[str] | None:
    """Resolve --status into a status filter set.

    Absent applies LIST_DEFAULT_STATUSES (hides new/archived); 'all' is the
    explicit no-filter escape hatch (legacy parity).
    """
    if raw is None:
        return LIST_DEFAULT_STATUSES
    values = frozenset(s.strip() for s in raw.split(",") if s.strip())
    if "all" in values:
        return None
    return values or LIST_DEFAULT_STATUSES


async def _run_list(app: AppContext, filters: StatusFilter) -> list[StatusInfo]:
    async def action() -> list[StatusInfo]:
        store = cast(WorkflowStore, app["store"])
        return await store.list_statuses(filters)

    return await run_with_store(app, action)


def _clip(text: str | None, width: int) -> str:
    """Hard-truncate to width chars (legacy tabular parity)."""
    return (text or "")[:width]


def _fmt_date(dt: datetime | None) -> str:
    return dt.date().isoformat() if dt else ""


def _print_json(rows: list[StatusInfo]) -> None:
    data = [
        {
            "id": r.job_id,
            "status": str(r.status),
            "company": r.company,
            "title": r.title,
            "last_status_change_at": r.last_status_change_at.isoformat(),
            "next_followup_at": (
                r.next_followup_at.isoformat() if r.next_followup_at else None
            ),
        }
        for r in rows
    ]
    click.echo(json.dumps(data))


def _print_markdown(rows: list[StatusInfo]) -> None:
    click.echo("| id | status | company | title | last_change | followup |")
    click.echo("|----|--------|---------|-------|-------------|----------|")
    for r in rows:
        click.echo(
            f"| {r.job_id} | {r.status} | {_clip(r.company, _COMPANY_WIDTH)} "
            f"| {_clip(r.title, _TITLE_WIDTH)} "
            f"| {_fmt_date(r.last_status_change_at)} "
            f"| {_fmt_date(r.next_followup_at)} |"
        )


def _print_plain(rows: list[StatusInfo]) -> None:
    for r in rows:
        click.echo(
            f"{r.job_id:>6}  {r.status:<12}  "
            f"{_clip(r.company, _COMPANY_WIDTH):<{_COMPANY_WIDTH}}  "
            f"{_clip(r.title, _TITLE_WIDTH):<{_TITLE_WIDTH}}  "
            f"{_fmt_date(r.last_status_change_at)}  "
            f"{_fmt_date(r.next_followup_at)}"
        )


# ── stats ─────────────────────────────────────────────────────────────


@click.command(name="stats", help="Show application statistics.")
@click.option(
    "--by-resume",
    is_flag=True,
    help="Include per-variant breakdown.",
)
@click.option(
    "--window",
    "window_days",
    default=30,
    show_default=True,
    type=int,
    help="Lookback window in days.",
)
@click.pass_context
def stats(
    ctx: click.Context,
    by_resume: bool,
    window_days: int,
) -> None:
    """Print application statistics.

    Args:
        ctx: Click invocation context.
        by_resume: Include per-variant breakdown.
        window_days: Lookback window in days.
    """
    app = require_app(ctx)
    asyncio.run(_run_stats(app, by_resume=by_resume, window_days=window_days))


async def _run_stats(
    app: AppContext,
    *,
    by_resume: bool,
    window_days: int,
) -> None:
    async def action() -> None:
        store = cast(ApplicationStore, app["store"])
        svc = ApplicationService(store, app["logger"])
        result = await svc.stats(since_days_ago=window_days, by_resume=by_resume)
        click.echo(f"Applied: {result.applied_count}")
        click.echo(f"Responses: {result.response_count}")
        click.echo(f"Interviews: {result.interview_count}")
        click.echo(f"Offers: {result.offer_count}")
        click.echo(f"Rejections: {result.rejection_count}")
        if result.median_days_to_response is not None:
            click.echo(f"Median days to response: {result.median_days_to_response:.1f}")
        if result.by_resume:
            for name, vs in result.by_resume.items():
                click.echo(
                    f"  {name}: sent={vs.sent} "
                    f"responses={vs.responses} "
                    f"interviews={vs.interviews} "
                    f"offers={vs.offers}"
                )

    await run_with_store(app, action)


__all__ = ["list_cmd", "stats"]
