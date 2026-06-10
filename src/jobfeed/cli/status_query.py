"""Click commands for read-only status queries: list and stats."""

from __future__ import annotations

import asyncio
import json
from typing import cast

import click

from jobfeed.cli import AppContext, require_app, run_with_store
from jobfeed.domain.models_status import StatusInfo
from jobfeed.services.application import ApplicationService, ApplicationStore
from jobfeed.services.workflow import WorkflowStore

# ── list ──────────────────────────────────────────────────────────────


@click.command(name="list", help="List jobs by status.")
@click.option(
    "--status",
    "status_filter",
    default=None,
    help="Comma-separated status values to filter by.",
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
@click.option("--md", "markdown", is_flag=True, help="Markdown table output.")
@click.option("--json", "as_json", is_flag=True, help="JSON array output.")
@click.pass_context
def list_cmd(ctx: click.Context, /, **kwargs: object) -> None:
    """List jobs filtered by status and follow-up state.

    Args:
        ctx: Click invocation context.
        kwargs: Click option values keyed by option name.
    """
    app = require_app(ctx)
    status_filter = cast(str | None, kwargs["status_filter"])
    statuses: frozenset[str] | None = None
    if status_filter:
        statuses = frozenset(s.strip() for s in status_filter.split(","))
    rows = asyncio.run(
        _run_list(
            app,
            statuses=statuses,
            needs_followup=cast(bool, kwargs["needs_followup"]),
            no_response_days=cast(int | None, kwargs["no_response_days"]),
        )
    )
    if cast(bool, kwargs["as_json"]):
        _print_json(rows)
    elif cast(bool, kwargs["markdown"]):
        _print_markdown(rows)
    else:
        _print_plain(rows)


async def _run_list(
    app: AppContext,
    *,
    statuses: frozenset[str] | None,
    needs_followup: bool,
    no_response_days: int | None,
) -> list[StatusInfo]:
    async def action() -> list[StatusInfo]:
        store = cast(WorkflowStore, app["store"])
        return await store.list_statuses(
            statuses=statuses,
            needs_followup=needs_followup,
            no_response_days=no_response_days,
        )

    return await run_with_store(app, action)


def _print_json(rows: list[StatusInfo]) -> None:
    data = [
        {
            "id": r.job_id,
            "status": str(r.status),
            "next_followup_at": (
                r.next_followup_at.isoformat() if r.next_followup_at else None
            ),
        }
        for r in rows
    ]
    click.echo(json.dumps(data))


def _print_markdown(rows: list[StatusInfo]) -> None:
    click.echo("| id | status | next_followup_at |")
    click.echo("|----|--------|------------------|")
    for r in rows:
        fu = r.next_followup_at.isoformat() if r.next_followup_at else ""
        click.echo(f"| {r.job_id} | {r.status} | {fu} |")


def _print_plain(rows: list[StatusInfo]) -> None:
    for r in rows:
        fu_str = ""
        if r.next_followup_at:
            fu_str = f"  followup={r.next_followup_at.isoformat()}"
        click.echo(f"{r.job_id}  {r.status}{fu_str}")


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
