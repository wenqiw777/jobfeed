"""Click commands for status mutations: mark, archive, note."""

from __future__ import annotations

import asyncio
from typing import cast

import click

from jobfeed.cli import AppContext, require_app, run_with_store
from jobfeed.domain.models_status import BulkResult
from jobfeed.domain.status import STATUS_VALUES
from jobfeed.services.workflow import WorkflowService, WorkflowStore

_STATUS_CHOICES = sorted(STATUS_VALUES)


def _build_workflow(app: AppContext) -> WorkflowService:
    """Build WorkflowService from the app context store."""
    store = cast(WorkflowStore, app["store"])
    return WorkflowService(store, app["logger"])


# ── mark ──────────────────────────────────────────────────────────────


@click.command(name="mark", help="Transition one or more jobs to a new status.")
@click.argument("ids", nargs=-1, required=True)
@click.option(
    "--status",
    type=click.Choice(_STATUS_CHOICES),
    default=None,
    help="Target status (required unless --restore).",
)
@click.option("--bulk", is_flag=True, help="Apply twin-cluster cascade.")
@click.option(
    "--note",
    "note_text",
    default=None,
    help="Append a note after transition.",
)
@click.option("--force", is_flag=True, help="Bypass the transition graph.")
@click.option(
    "--i-mean-it",
    is_flag=True,
    help="Confirm destructive forced transitions.",
)
@click.option(
    "--restore",
    is_flag=True,
    help="Restore to most recent non-terminal status.",
)
@click.option(
    "--resume",
    "resume_variant",
    default=None,
    help="Resume variant name.",
)
@click.pass_context
def mark(ctx: click.Context, /, **kwargs: object) -> None:
    """Transition jobs by id.

    Args:
        ctx: Click invocation context.
        kwargs: Click option values keyed by option name.
    """
    app = require_app(ctx)
    asyncio.run(_run_mark(app, kwargs))


async def _run_mark(app: AppContext, opts: dict[str, object]) -> None:
    ids = cast(tuple[str, ...], opts["ids"])
    status = cast(str | None, opts["status"])
    bulk = cast(bool, opts["bulk"])
    note_text = cast(str | None, opts["note_text"])
    force = cast(bool, opts["force"])
    i_mean_it = cast(bool, opts["i_mean_it"])
    restore = cast(bool, opts["restore"])
    resume_variant = cast(str | None, opts["resume_variant"])

    async def action() -> None:
        svc = _build_workflow(app)
        if restore:
            for jid in ids:
                result = await svc.restore(jid)
                click.echo(f"{jid} restored to {result}")
            return

        if status is None:
            raise click.UsageError("--status is required unless --restore is used")

        if bulk:
            if note_text or resume_variant:
                raise click.UsageError(
                    "--note and --resume are not supported with --bulk"
                )
            items = [(jid, status) for jid in ids]
            br: BulkResult = await svc.transition_bulk(
                items, force=force, i_mean_it=i_mean_it
            )
            click.echo(
                f"Bulk: {br.succeeded} succeeded, "
                f"{len(br.failed)} failed, {br.skipped} skipped"
            )
            return

        for jid in ids:
            result = await svc.transition(
                jid,
                status,
                force=force,
                i_mean_it=i_mean_it,
                note=note_text,
                resume_variant=resume_variant,
            )
            click.echo(f"{jid} -> {result}")

    await run_with_store(app, action)


# ── archive ───────────────────────────────────────────────────────────


@click.command(
    name="archive",
    help="Archive one or more jobs (alias for mark <ids> archived).",
)
@click.argument("ids", nargs=-1, required=True)
@click.option("--force", is_flag=True, help="Bypass the transition graph.")
@click.pass_context
def archive(
    ctx: click.Context,
    ids: tuple[str, ...],
    force: bool,
) -> None:
    """Archive jobs by id.

    Args:
        ctx: Click invocation context.
        ids: One or more job ids.
        force: Bypass graph validation.
    """
    app = require_app(ctx)
    asyncio.run(_run_archive(app, ids=ids, force=force))


async def _run_archive(
    app: AppContext,
    *,
    ids: tuple[str, ...],
    force: bool,
) -> None:
    async def action() -> None:
        svc = _build_workflow(app)
        for jid in ids:
            result = await svc.transition(jid, "archived", force=force)
            click.echo(f"{jid} -> {result}")

    await run_with_store(app, action)


# ── note ──────────────────────────────────────────────────────────────


@click.command(name="note", help="Append a note to a job.")
@click.argument("job_id")
@click.argument("text")
@click.pass_context
def note(ctx: click.Context, job_id: str, text: str) -> None:
    """Append a timestamped note.

    Args:
        ctx: Click invocation context.
        job_id: Store-assigned job identity.
        text: Note text to append.
    """
    app = require_app(ctx)
    asyncio.run(_run_note(app, job_id=job_id, text=text))


async def _run_note(app: AppContext, *, job_id: str, text: str) -> None:
    async def action() -> None:
        svc = _build_workflow(app)
        await svc.note(job_id, text)
        click.echo(f"Note added to {job_id}")

    await run_with_store(app, action)


__all__ = ["archive", "mark", "note"]
