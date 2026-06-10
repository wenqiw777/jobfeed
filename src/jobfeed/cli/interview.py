"""Click commands for interview round tracking."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import cast

import click

from jobfeed.cli import AppContext, require_app, run_with_store
from jobfeed.services.workflow import WorkflowService, WorkflowStore


def _build_workflow(app: AppContext) -> WorkflowService:
    """Build WorkflowService from the app context store."""
    store = cast(WorkflowStore, app["store"])
    return WorkflowService(store, app["logger"])


def _parse_datetime(value: str) -> datetime:
    """Parse a datetime string in ISO-8601 or common short formats.

    Args:
        value: Datetime string to parse.

    Returns:
        Parsed datetime object.

    Raises:
        click.BadParameter: If the string cannot be parsed.
    """
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(value, fmt)
            return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt
        except ValueError:
            continue
    raise click.BadParameter(
        f"cannot parse datetime: {value!r}  (expected YYYY-MM-DD or YYYY-MM-DDTHH:MM)"
    )


# ── interview group ──────────────────────────────────────────────────


@click.group(name="interview", help="Interview round tracking commands.")
def interview() -> None:
    """Interview subcommands: add, list, done."""


@interview.command(name="add", help="Add an interview round to a job.")
@click.argument("job_id")
@click.argument("label")
@click.option(
    "--scheduled-at",
    "scheduled_at_str",
    default=None,
    help="Scheduled interview time (YYYY-MM-DD or YYYY-MM-DDTHH:MM).",
)
@click.pass_context
def interview_add(
    ctx: click.Context,
    job_id: str,
    label: str,
    scheduled_at_str: str | None,
) -> None:
    """Add an interview round.

    Args:
        ctx: Click invocation context.
        job_id: Store-assigned job identity.
        label: Human-readable round label.
        scheduled_at_str: Optional scheduled time string.
    """
    app = require_app(ctx)
    scheduled_at = _parse_datetime(scheduled_at_str) if scheduled_at_str else None
    asyncio.run(_run_add(app, job_id=job_id, label=label, scheduled_at=scheduled_at))


async def _run_add(
    app: AppContext,
    *,
    job_id: str,
    label: str,
    scheduled_at: datetime | None,
) -> None:
    async def action() -> None:
        svc = _build_workflow(app)
        rnd = await svc.add_round(job_id, label, scheduled_at=scheduled_at)
        click.echo(f"Round {rnd.round_index} ({rnd.label}) added to {job_id}")

    await run_with_store(app, action)


# ── interview list ───────────────────────────────────────────────────


@interview.command(name="list", help="List interview rounds for a job.")
@click.argument("job_id")
@click.pass_context
def interview_list(ctx: click.Context, job_id: str) -> None:
    """List rounds for a job.

    Args:
        ctx: Click invocation context.
        job_id: Store-assigned job identity.
    """
    app = require_app(ctx)
    asyncio.run(_run_list(app, job_id=job_id))


async def _run_list(app: AppContext, *, job_id: str) -> None:
    async def action() -> None:
        svc = _build_workflow(app)
        rounds = await svc.list_rounds(job_id)
        if not rounds:
            click.echo(f"No interview rounds for {job_id}")
            return
        for rnd in rounds:
            done = " [done]" if rnd.completed_at else ""
            sched = (
                f"  scheduled={rnd.scheduled_at.isoformat()}"
                if rnd.scheduled_at
                else ""
            )
            click.echo(f"  {rnd.round_index}. {rnd.label}{done}{sched}")

    await run_with_store(app, action)


# ── interview done ───────────────────────────────────────────────────


@interview.command(name="done", help="Mark an interview round as completed.")
@click.argument("job_id")
@click.option(
    "--round",
    "round_index",
    default=None,
    type=int,
    help="Specific round index to complete (default: latest open).",
)
@click.option("--notes", default=None, help="Notes to attach to the round.")
@click.pass_context
def interview_done(
    ctx: click.Context,
    job_id: str,
    round_index: int | None,
    notes: str | None,
) -> None:
    """Complete an interview round.

    Args:
        ctx: Click invocation context.
        job_id: Store-assigned job identity.
        round_index: Specific round to complete.
        notes: Optional notes.
    """
    app = require_app(ctx)
    asyncio.run(_run_done(app, job_id=job_id, round_index=round_index, notes=notes))


async def _run_done(
    app: AppContext,
    *,
    job_id: str,
    round_index: int | None,
    notes: str | None,
) -> None:
    async def action() -> None:
        svc = _build_workflow(app)
        rnd = await svc.complete_round(job_id, round_index=round_index, notes=notes)
        click.echo(f"Round {rnd.round_index} ({rnd.label}) completed")

    await run_with_store(app, action)


__all__ = ["interview"]
