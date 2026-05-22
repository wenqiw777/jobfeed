"""Click command for rendering the current Markdown digest."""

from __future__ import annotations

import asyncio
from datetime import datetime

import click

from jobfeed.cli import AppContext, require_app, run_with_store


@click.command(name="digest", help="Render the current job digest.")
@click.option(
    "--cutoff-at",
    metavar="ISO_DATETIME",
    help="Timezone-aware ISO timestamp for splitting apply-tier jobs.",
)
@click.pass_context
def digest(ctx: click.Context, cutoff_at: str | None) -> None:
    """Render evaluated jobs as Markdown.

    Args:
        ctx: Click invocation context.
        cutoff_at: Optional ISO-8601 timestamp for new vs seen apply jobs.
    """
    app = require_app(ctx)
    markdown = asyncio.run(_run_digest(app, cutoff_at=_parse_cutoff_at(cutoff_at)))
    click.echo(markdown)


async def _run_digest(app: AppContext, *, cutoff_at: datetime | None) -> str:
    return await run_with_store(
        app,
        lambda: app["digest_service"].run(cutoff_at=cutoff_at),
    )


def _parse_cutoff_at(value: str | None) -> datetime | None:
    if value is None:
        return None
    try:
        cutoff_at = datetime.fromisoformat(value)
    except ValueError as exc:
        raise click.BadParameter("must be an ISO-8601 datetime") from exc
    if cutoff_at.tzinfo is None or cutoff_at.utcoffset() is None:
        raise click.BadParameter("must include a timezone offset")
    return cutoff_at


__all__ = ["digest"]
