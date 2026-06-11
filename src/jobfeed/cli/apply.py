"""Click commands for application recording and history.

The resume-snapshot subcommands live in ``cli/snapshots.py``.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import cast

import click

from jobfeed.cli import AppContext, require_app, run_with_store
from jobfeed.domain.models import ApplicationRecord
from jobfeed.services.application import (
    ApplicationService,
    ApplicationStore,
    ApplyRequest,
)

_METHOD_CHOICES = ("web", "referral", "email")


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
@click.option(
    "--method",
    "application_method",
    type=click.Choice(_METHOD_CHOICES),
    default="web",
    show_default=True,
    help="How the application was submitted.",
)
@click.option(
    "--notes",
    default=None,
    help="Free-form note stored on the application record.",
)
@click.pass_context
def apply_cmd(ctx: click.Context, /, **kwargs: object) -> None:
    """Record an application for a job.

    Reads the master resume from the configured path, plus optional
    tailored resume and cover letter files.

    Args:
        ctx: Click invocation context.
        kwargs: Click option values keyed by option name.
    """
    app = require_app(ctx)
    asyncio.run(_run_apply(app, kwargs))


async def _run_apply(app: AppContext, opts: dict[str, object]) -> None:
    job_id = cast(str, opts["job_id"])
    tailored_path = cast(Path | None, opts["tailored_path"])
    cover_letter_path = cast(Path | None, opts["cover_letter_path"])

    async def action() -> None:
        settings = app["settings"]
        master_resume = _read_file(Path(settings.llm.master_resume_path))
        tailored = _read_file(tailored_path) if tailored_path else None
        cover_letter = _read_file(cover_letter_path) if cover_letter_path else None
        verdict_snap, fit_snap, hooks_snap = await _stage_b_snapshots(app, job_id)

        req = ApplyRequest(
            job_id=job_id,
            master_resume=master_resume,
            tailored_resume=tailored,
            cover_letter=cover_letter,
            variant=cast(str | None, opts["variant"]),
            application_method=cast(str, opts["application_method"]),
            notes=cast(str | None, opts["notes"]),
            verdict_snapshot=verdict_snap,
            fit_snapshot=fit_snap,
            hooks_snapshot=hooks_snap,
        )
        svc = _build_application_svc(app)
        is_new = await svc.apply(req)
        if not is_new:
            click.echo(f"Already applied to {job_id}")
            return
        click.echo(f"Application recorded for {job_id}")
        notice = await svc.reapply_notice(job_id)
        if notice is not None:
            click.echo(notice)

    await run_with_store(app, action)


async def _stage_b_snapshots(
    app: AppContext,
    job_id: str,
) -> tuple[str | None, str | None, str | None]:
    """Capture Stage B verdict/fit/hooks JSON snapshots when available.

    Args:
        app: Initialized application context.
        job_id: Store-assigned job identity.

    Returns:
        (verdict, fit_analysis, resume_hooks) JSON strings or Nones.
    """
    evaluation = await app["store"].get_evaluation(job_id)
    if evaluation is None or evaluation.stage_b is None:
        return (None, None, None)
    blocks = evaluation.stage_b.raw_blocks or {}

    def _dump(key: str) -> str | None:
        return json.dumps(blocks[key], sort_keys=True) if key in blocks else None

    return (_dump("verdict"), _dump("fit_analysis"), _dump("resume_hooks"))


# ── apply-history ─────────────────────────────────────────────────────


@click.command(name="apply-history", help="List recent job applications.")
@click.option(
    "--limit",
    default=50,
    show_default=True,
    type=click.IntRange(min=1),
    help="Maximum records to show.",
)
@click.option(
    "--resume",
    "resume_hash_prefix",
    default=None,
    help="Only applications whose resume hash starts with this prefix.",
)
@click.pass_context
def apply_history(
    ctx: click.Context,
    limit: int,
    resume_hash_prefix: str | None,
) -> None:
    """List recent applications.

    Args:
        ctx: Click invocation context.
        limit: Maximum number of records.
        resume_hash_prefix: Optional resume-hash prefix filter.
    """
    app = require_app(ctx)
    asyncio.run(_run_history(app, limit=limit, resume_hash_prefix=resume_hash_prefix))


async def _run_history(
    app: AppContext,
    *,
    limit: int,
    resume_hash_prefix: str | None,
) -> None:
    async def action() -> None:
        svc = _build_application_svc(app)
        records = await svc.apply_history(
            limit=limit,
            resume_hash_prefix=resume_hash_prefix,
        )
        if not records:
            click.echo("No applications found.")
            return
        for rec in records:
            click.echo(_history_line(rec))

    await run_with_store(app, action)


def _history_line(rec: ApplicationRecord) -> str:
    """Format one apply-history row: id, timestamp, method, notes."""
    parts = [rec.job_id, rec.applied_at.strftime("%Y-%m-%d %H:%M")]
    if rec.application_method:
        parts.append(rec.application_method)
    if rec.notes:
        parts.append(rec.notes)
    return "  ".join(parts)


__all__ = ["apply_cmd", "apply_history"]
