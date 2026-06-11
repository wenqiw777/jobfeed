"""Click commands for resume snapshots: show, list, diff.

Moved out of ``cli/apply.py`` to keep both modules under the 300-line gate.
``show`` and ``diff`` accept unique hash prefixes; ``list`` without a job id
shows every stored snapshot with its usage count.
"""

from __future__ import annotations

import asyncio
from typing import cast

import click

from jobfeed.cli import AppContext, require_app, run_with_store
from jobfeed.domain.errors import SnapshotAmbiguousError, SnapshotNotFoundError
from jobfeed.domain.models import ResumeSnapshot, ResumeSnapshotSummary
from jobfeed.services.application import ApplicationService, ApplicationStore

_HASH_DISPLAY_LEN = 12
_SOURCE_CHOICES = ("master", "tailored")


def _build_application_svc(app: AppContext) -> ApplicationService:
    """Build ApplicationService from the app context store."""
    store = cast(ApplicationStore, app["store"])
    return ApplicationService(store, app["logger"])


def _snapshot_lookup_error(exc: LookupError) -> click.ClickException:
    """Map a snapshot lookup failure to a distinct user-facing error.

    Not-found keeps the store message naming the unmatched prefix;
    ambiguity additionally asks for more characters.

    Args:
        exc: SnapshotNotFoundError or SnapshotAmbiguousError.

    Returns:
        Click exception carrying the user-facing message.
    """
    if isinstance(exc, SnapshotAmbiguousError):
        return click.ClickException(f"{exc}; use more characters")
    return click.ClickException(str(exc))


async def _resolve_prefix(svc: ApplicationService, prefix: str) -> ResumeSnapshot:
    """Resolve a snapshot prefix, mapping lookup failures to Click errors.

    Args:
        svc: Application service.
        prefix: Hash prefix to resolve.

    Returns:
        The single matching snapshot.

    Raises:
        click.ClickException: If the prefix matches zero or several snapshots.
    """
    try:
        return await svc.get_snapshot_by_prefix(prefix)
    except (SnapshotNotFoundError, SnapshotAmbiguousError) as exc:
        raise _snapshot_lookup_error(exc) from exc


@click.group(name="snapshots", help="Resume snapshot commands.")
def snapshots() -> None:
    """Resume snapshot subcommands."""


@snapshots.command(name="show", help="Show a resume snapshot by hash or prefix.")
@click.argument("prefix")
@click.pass_context
def snapshots_show(ctx: click.Context, prefix: str) -> None:
    """Print a stored resume snapshot resolved by unique hash prefix.

    Args:
        ctx: Click invocation context.
        prefix: SHA-256 content hash or a unique prefix of one.
    """
    app = require_app(ctx)
    asyncio.run(_run_snapshot_show(app, prefix=prefix))


async def _run_snapshot_show(app: AppContext, *, prefix: str) -> None:
    async def action() -> None:
        svc = _build_application_svc(app)
        snap = await _resolve_prefix(svc, prefix)
        click.echo(snap.content)

    await run_with_store(app, action)


@snapshots.command(name="list", help="List resume snapshots (all, or one job's).")
@click.argument("job_id", required=False, default=None)
@click.option(
    "--source",
    type=click.Choice(_SOURCE_CHOICES),
    default=None,
    help="Filter the global list by snapshot source.",
)
@click.pass_context
def snapshots_list(ctx: click.Context, job_id: str | None, source: str | None) -> None:
    """List snapshots: globally with usage counts, or one job's hashes.

    Args:
        ctx: Click invocation context.
        job_id: Optional store-assigned job identity; when given, lists that
            job's application hashes (original per-job behavior).
        source: Optional source filter, valid only for the global list.

    Raises:
        click.UsageError: If --source is combined with a JOB_ID.
    """
    app = require_app(ctx)
    if job_id is not None and source is not None:
        raise click.UsageError("--source applies to the global list; omit JOB_ID")
    if job_id is not None:
        asyncio.run(_run_snapshot_list_for_job(app, job_id=job_id))
        return
    asyncio.run(_run_snapshot_list_global(app, source=source))


async def _run_snapshot_list_global(app: AppContext, *, source: str | None) -> None:
    async def action() -> None:
        svc = _build_application_svc(app)
        summaries = await svc.list_snapshots(source=source)
        if not summaries:
            click.echo("No snapshots found.")
            return
        for row in summaries:
            click.echo(_summary_line(row))

    await run_with_store(app, action)


def _summary_line(row: ResumeSnapshotSummary) -> str:
    """Format one global-list row: short hash, capture date, source, usage."""
    return (
        f"{row.resume_hash[:_HASH_DISPLAY_LEN]}  "
        f"{row.captured_at.date().isoformat()}  "
        f"{row.source:<8}  "
        f"used={row.usage_count}"
    )


async def _run_snapshot_list_for_job(app: AppContext, *, job_id: str) -> None:
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


@snapshots.command(name="diff", help="Diff two resume snapshots by hash or prefix.")
@click.argument("prefix_a")
@click.argument("prefix_b")
@click.pass_context
def snapshots_diff(ctx: click.Context, prefix_a: str, prefix_b: str) -> None:
    """Show a unified diff between two prefix-resolved snapshots.

    Args:
        ctx: Click invocation context.
        prefix_a: First snapshot hash or unique prefix.
        prefix_b: Second snapshot hash or unique prefix.
    """
    app = require_app(ctx)
    asyncio.run(_run_snapshot_diff(app, prefix_a=prefix_a, prefix_b=prefix_b))


async def _run_snapshot_diff(
    app: AppContext,
    *,
    prefix_a: str,
    prefix_b: str,
) -> None:
    async def action() -> None:
        svc = _build_application_svc(app)
        try:
            diff_text = await svc.diff_snapshots(prefix_a, prefix_b)
        except (SnapshotNotFoundError, SnapshotAmbiguousError) as exc:
            raise _snapshot_lookup_error(exc) from exc
        if diff_text:
            click.echo(diff_text)
        else:
            click.echo("Snapshots are identical.")

    await run_with_store(app, action)


__all__ = ["snapshots"]
