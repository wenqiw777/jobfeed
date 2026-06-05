"""Click command for maintenance operations."""

from __future__ import annotations

import asyncio

import click

from jobfeed.cli import AppContext, require_app, run_with_store
from jobfeed.ports.store_ops import StoreOpsMixin


@click.command(
    name="mark-stale-closed",
    help=(
        "Mark stale no-JD postings as closed. "
        "Targets rows with no usable JD (quality IS NULL, 'missing', or 'abandoned') "
        "that were discovered more than --older-than-days days ago and are still open."
    ),
)
@click.option(
    "--older-than-days",
    default=30,
    show_default=True,
    type=int,
    help="Discovery-age threshold in days.",
)
@click.option(
    "--apply",
    "apply",
    is_flag=True,
    default=False,
    help="Write changes. Omit to run in dry-run mode (default).",
)
@click.pass_context
def mark_stale_closed(
    ctx: click.Context,
    older_than_days: int,
    apply: bool,
) -> None:
    """Backfill closed_at on stale no-JD postings.

    Args:
        ctx: Click invocation context.
        older_than_days: Discovery-age threshold.
        apply: When True, write changes; otherwise dry-run.
    """
    app = require_app(ctx)
    dry_run = not apply
    count = asyncio.run(_run_mark_stale(app, older_than_days, dry_run))
    if dry_run:
        click.echo(f"Would close {count} stale jobs (dry-run; pass --apply to write)")
    else:
        click.echo(f"Closed {count} stale jobs.")


async def _run_mark_stale(
    app: AppContext,
    older_than_days: int,
    dry_run: bool,
) -> int:
    async def action() -> int:
        store = app["store"]
        if not isinstance(store, StoreOpsMixin):
            raise click.ClickException(
                f"Store does not support maintenance ops: {type(store).__name__}"
            )
        return await store.mark_stale_jobs_closed(
            older_than_days=older_than_days,
            dry_run=dry_run,
        )

    return await run_with_store(app, action)


__all__ = ["mark_stale_closed"]
