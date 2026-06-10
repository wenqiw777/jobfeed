"""Click commands for resume snapshots: show, list, diff.

Moved out of ``cli/apply.py`` to keep both modules under the 300-line gate.
"""

from __future__ import annotations

import asyncio
from typing import cast

import click

from jobfeed.cli import AppContext, require_app, run_with_store
from jobfeed.services.application import ApplicationService, ApplicationStore


def _build_application_svc(app: AppContext) -> ApplicationService:
    """Build ApplicationService from the app context store."""
    store = cast(ApplicationStore, app["store"])
    return ApplicationService(store, app["logger"])


@click.group(name="snapshots", help="Resume snapshot commands.")
def snapshots() -> None:
    """Resume snapshot subcommands."""


@snapshots.command(name="show", help="Show a resume snapshot by hash.")
@click.argument("resume_hash")
@click.pass_context
def snapshots_show(ctx: click.Context, resume_hash: str) -> None:
    """Print a stored resume snapshot.

    Args:
        ctx: Click invocation context.
        resume_hash: SHA-256 content hash.
    """
    app = require_app(ctx)
    asyncio.run(_run_snapshot_show(app, resume_hash=resume_hash))


async def _run_snapshot_show(app: AppContext, *, resume_hash: str) -> None:
    async def action() -> None:
        svc = _build_application_svc(app)
        snap = await svc.get_snapshot(resume_hash)
        if snap is None:
            raise click.ClickException(f"snapshot not found: {resume_hash}")
        click.echo(snap.content)

    await run_with_store(app, action)


@snapshots.command(name="list", help="List resume snapshot hashes for a job.")
@click.argument("job_id")
@click.pass_context
def snapshots_list(ctx: click.Context, job_id: str) -> None:
    """Show resume snapshot hashes associated with a job's application.

    Args:
        ctx: Click invocation context.
        job_id: Store-assigned job identity.
    """
    app = require_app(ctx)
    asyncio.run(_run_snapshot_list(app, job_id=job_id))


async def _run_snapshot_list(app: AppContext, *, job_id: str) -> None:
    async def action() -> None:
        svc = _build_application_svc(app)
        rec = await svc.get_application(job_id)
        if rec is None:
            raise click.ClickException(f"no application found for job {job_id}")
        ts = rec.applied_at.strftime("%Y-%m-%d %H:%M")
        click.echo(f"applied: {ts}")
        if rec.master_resume_hash:
            click.echo(f"  master:   {rec.master_resume_hash}")
        if rec.tailored_resume_hash:
            click.echo(f"  tailored: {rec.tailored_resume_hash}")

    await run_with_store(app, action)


@snapshots.command(name="diff", help="Diff two resume snapshots.")
@click.argument("hash_a")
@click.argument("hash_b")
@click.pass_context
def snapshots_diff(ctx: click.Context, hash_a: str, hash_b: str) -> None:
    """Show unified diff between two snapshots.

    Args:
        ctx: Click invocation context.
        hash_a: First snapshot hash.
        hash_b: Second snapshot hash.
    """
    app = require_app(ctx)
    asyncio.run(_run_snapshot_diff(app, hash_a=hash_a, hash_b=hash_b))


async def _run_snapshot_diff(
    app: AppContext,
    *,
    hash_a: str,
    hash_b: str,
) -> None:
    async def action() -> None:
        svc = _build_application_svc(app)
        diff_text = await svc.diff_snapshots(hash_a, hash_b)
        if diff_text:
            click.echo(diff_text)
        else:
            click.echo("Snapshots are identical.")

    await run_with_store(app, action)


__all__ = ["snapshots"]
