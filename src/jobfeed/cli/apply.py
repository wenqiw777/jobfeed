"""Click commands for application recording and resume snapshots."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import cast

import click

from jobfeed.cli import AppContext, require_app, run_with_store
from jobfeed.services.application import (
    ApplicationService,
    ApplicationStore,
    ApplyRequest,
)


def _build_application_svc(app: AppContext) -> ApplicationService:
    """Build ApplicationService from the app context store."""
    store = cast(ApplicationStore, app["store"])
    return ApplicationService(store, app["logger"])


def _read_file(path: Path) -> str:
    """Read a text file, raising ClickException on failure.

    Args:
        path: Path to the file to read.

    Returns:
        File contents as a string.

    Raises:
        click.ClickException: If the file cannot be read.
    """
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise click.ClickException(f"cannot read {path}: {exc}") from exc


# ── apply ─────────────────────────────────────────────────────────────


@click.command(name="apply", help="Record a job application with resume snapshots.")
@click.argument("job_id")
@click.option(
    "--tailored",
    "tailored_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Path to a tailored resume file.",
)
@click.option(
    "--cover-letter",
    "cover_letter_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Path to a cover letter file.",
)
@click.option("--variant", default=None, help="Resume variant name for A/B tracking.")
@click.pass_context
def apply_cmd(
    ctx: click.Context,
    job_id: str,
    tailored_path: Path | None,
    cover_letter_path: Path | None,
    variant: str | None,
) -> None:
    """Record an application for a job.

    Reads the master resume from the configured path, plus optional
    tailored resume and cover letter files.

    Args:
        ctx: Click invocation context.
        job_id: Store-assigned job identity.
        tailored_path: Optional tailored resume file.
        cover_letter_path: Optional cover letter file.
        variant: Optional resume variant name.
    """
    app = require_app(ctx)
    asyncio.run(
        _run_apply(
            app,
            job_id=job_id,
            tailored_path=tailored_path,
            cover_letter_path=cover_letter_path,
            variant=variant,
        )
    )


async def _run_apply(
    app: AppContext,
    *,
    job_id: str,
    tailored_path: Path | None,
    cover_letter_path: Path | None,
    variant: str | None,
) -> None:
    async def action() -> None:
        settings = app["settings"]
        master_path = Path(settings.llm.master_resume_path)
        master_resume = _read_file(master_path)
        tailored = _read_file(tailored_path) if tailored_path else None
        cover_letter = _read_file(cover_letter_path) if cover_letter_path else None

        # Capture Stage B evaluation snapshots if available.
        verdict_snap: str | None = None
        fit_snap: str | None = None
        hooks_snap: str | None = None
        store = app["store"]
        evaluation = await store.get_evaluation(job_id)
        if evaluation is not None and evaluation.stage_b is not None:
            blocks = evaluation.stage_b.raw_blocks or {}
            if "verdict" in blocks:
                verdict_snap = json.dumps(blocks["verdict"], sort_keys=True)
            if "fit_analysis" in blocks:
                fit_snap = json.dumps(blocks["fit_analysis"], sort_keys=True)
            if "resume_hooks" in blocks:
                hooks_snap = json.dumps(blocks["resume_hooks"], sort_keys=True)

        req = ApplyRequest(
            job_id=job_id,
            master_resume=master_resume,
            tailored_resume=tailored,
            cover_letter=cover_letter,
            variant=variant,
            verdict_snapshot=verdict_snap,
            fit_snapshot=fit_snap,
            hooks_snapshot=hooks_snap,
        )
        svc = _build_application_svc(app)
        is_new = await svc.apply(req)
        if is_new:
            click.echo(f"Application recorded for {job_id}")
        else:
            click.echo(f"Already applied to {job_id}")

    await run_with_store(app, action)


# ── apply-history ─────────────────────────────────────────────────────


@click.command(name="apply-history", help="List recent job applications.")
@click.option(
    "--limit",
    default=50,
    show_default=True,
    type=click.IntRange(min=1),
    help="Maximum records to show.",
)
@click.pass_context
def apply_history(ctx: click.Context, limit: int) -> None:
    """List recent applications.

    Args:
        ctx: Click invocation context.
        limit: Maximum number of records.
    """
    app = require_app(ctx)
    asyncio.run(_run_history(app, limit=limit))


async def _run_history(app: AppContext, *, limit: int) -> None:
    async def action() -> None:
        svc = _build_application_svc(app)
        records = await svc.apply_history(limit=limit)
        if not records:
            click.echo("No applications found.")
            return
        for rec in records:
            ts = rec.applied_at.strftime("%Y-%m-%d %H:%M")
            click.echo(f"{rec.job_id}  {ts}")

    await run_with_store(app, action)


# ── snapshots ─────────────────────────────────────────────────────────


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


__all__ = ["apply_cmd", "apply_history", "snapshots"]
