"""Click commands for status mutations: mark, archive, note, followup."""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import cast

import click

from jobfeed.cli import AppContext, require_app, run_with_store
from jobfeed.cli._window import parse_window
from jobfeed.domain.models_status import (
    BulkResult,
    BulkTransitionRequest,
    TransitionRequest,
)
from jobfeed.domain.status import STATUS_VALUES
from jobfeed.ports.store_canonical import CanonicalWorkflowStore
from jobfeed.services.workflow import WorkflowService, WorkflowStore

_STATUS_CHOICES = sorted(STATUS_VALUES)


def _build_workflow(app: AppContext) -> WorkflowService:
    """Build WorkflowService from the app context store."""
    store = cast(WorkflowStore, app["store"])
    return WorkflowService(store, app["logger"])


async def _real_id(app: AppContext, source_id: str) -> str | None:
    resolver = (
        cast(CanonicalWorkflowStore, app["store"]).resolve_real_job_id
        if hasattr(type(app["store"]), "resolve_real_job_id")
        else None
    )
    return await resolver(source_id) if resolver is not None else None


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
                real_id = await _real_id(app, jid)
                result = (
                    await cast(CanonicalWorkflowStore, app["store"]).restore_real_job(
                        real_id
                    )
                    if real_id is not None
                    else await svc.restore(jid)
                )
                click.echo(f"{jid} restored to {result}")
            return

        if status is None:
            raise click.UsageError("--status is required unless --restore is used")

        if bulk:
            if note_text or resume_variant:
                raise click.UsageError(
                    "--note and --resume are not supported with --bulk"
                )
            br = await _mark_bulk(app, svc, ids, status, force, i_mean_it)
            click.echo(
                f"Bulk: {br.succeeded} succeeded, "
                f"{len(br.failed)} failed, {br.skipped} skipped"
            )
            return

        for jid in ids:
            result = await _mark_one(
                app, svc, jid, status, force, i_mean_it, resume_variant, note_text
            )
            click.echo(f"{jid} -> {result}")

    await run_with_store(app, action)


async def _mark_bulk(  # noqa: PLR0913
    app: AppContext,
    svc: WorkflowService,
    ids: tuple[str, ...],
    status: str,
    force: bool,
    i_mean_it: bool,
) -> BulkResult:
    parents = [await _real_id(app, jid) for jid in ids]
    if any(parent is not None for parent in parents):
        if any(parent is None for parent in parents):
            raise click.ClickException("bulk contains an unresolved source ID")
        return await cast(
            CanonicalWorkflowStore, app["store"]
        ).transition_real_jobs_bulk(
            BulkTransitionRequest(
                items=[(parent, status) for parent in parents if parent],
                reason_selected="bulk_selected",
                reason_cascade="bulk_cascade",
                force=force,
                i_mean_it=i_mean_it,
            )
        )
    return await svc.transition_bulk(
        [(jid, status) for jid in ids], force=force, i_mean_it=i_mean_it
    )


async def _mark_one(  # noqa: PLR0913
    app: AppContext,
    svc: WorkflowService,
    jid: str,
    status: str,
    force: bool,
    i_mean_it: bool,
    resume_variant: str | None,
    note_text: str | None,
) -> str:
    real_id = await _real_id(app, jid)
    req = TransitionRequest(
        job_id=real_id or jid,
        new_status=status,
        force=force,
        i_mean_it=i_mean_it,
        resume_variant=resume_variant,
    )
    if real_id is None:
        return await svc.transition(req, note=note_text)
    if resume_variant is not None:
        await cast(WorkflowStore, app["store"]).register_resume_variant(
            name=resume_variant
        )
    result = await cast(
        CanonicalWorkflowStore, app["store"]
    ).transition_real_job_status(req)
    if note_text is not None:
        await cast(CanonicalWorkflowStore, app["store"]).append_real_job_note(
            real_job_id=real_id, text=note_text
        )
    return result


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
            real_id = await _real_id(app, jid)
            req = TransitionRequest(
                job_id=real_id or jid, new_status="archived", force=force
            )
            result = (
                await cast(
                    CanonicalWorkflowStore, app["store"]
                ).transition_real_job_status(req)
                if real_id is not None
                else await svc.transition(req)
            )
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
        real_id = await _real_id(app, job_id)
        if real_id is not None:
            await cast(CanonicalWorkflowStore, app["store"]).append_real_job_note(
                real_job_id=real_id, text=text
            )
        else:
            await svc.note(job_id, text)
        click.echo(f"Note added to {job_id}")

    await run_with_store(app, action)


# ── followup ──────────────────────────────────────────────────────────


@click.command(name="followup", help="Schedule the next follow-up for a job.")
@click.argument("job_id")
@click.option(
    "--in",
    "window",
    default="7d",
    show_default=True,
    help="When to follow up: Nd (days), Nw (weeks), or YYYY-MM-DD.",
)
@click.pass_context
def followup(ctx: click.Context, job_id: str, window: str) -> None:
    """Set the next follow-up date for a job.

    Args:
        ctx: Click invocation context.
        job_id: Store-assigned job identity.
        window: Forward window or absolute date.
    """
    app = require_app(ctx)
    at = parse_window(window)
    asyncio.run(_run_followup(app, job_id=job_id, at=at))


async def _run_followup(app: AppContext, *, job_id: str, at: datetime) -> None:
    async def action() -> None:
        svc = _build_workflow(app)
        real_id = await _real_id(app, job_id)
        was_set = (
            await cast(CanonicalWorkflowStore, app["store"]).set_real_job_followup(
                real_job_id=real_id, at=at
            )
            if real_id is not None
            else await svc.set_followup(job_id=job_id, at=at)
        )
        if not was_set:
            raise click.ClickException(f"job not found: {job_id}")
        click.echo(f"Follow-up for {job_id} set to {at.date().isoformat()}")

    await run_with_store(app, action)


__all__ = ["archive", "followup", "mark", "note"]
