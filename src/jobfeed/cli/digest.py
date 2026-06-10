"""Click command for rendering the current Markdown digest."""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path

import click

from jobfeed.cli import AppContext, require_app, run_with_store


@click.command(name="digest", help="Render the current job digest.")
@click.option(
    "--cutoff-at",
    metavar="ISO_DATETIME",
    help="Timezone-aware ISO timestamp for splitting apply-tier jobs.",
)
@click.option(
    "--top",
    type=click.IntRange(min=1),
    default=None,
    help="Cap each verdict group at N rows.",
)
@click.option(
    "--quiet",
    is_flag=True,
    help="Suppress stdout echo (requires digest.output_dir).",
)
@click.pass_context
def digest(
    ctx: click.Context,
    cutoff_at: str | None,
    top: int | None,
    quiet: bool,
) -> None:
    """Render evaluated jobs as Markdown.

    Args:
        ctx: Click invocation context.
        cutoff_at: Optional ISO-8601 timestamp for new vs seen apply jobs.
        top: Optional per-group row cap.
        quiet: Suppress stdout echo; only valid with a configured output_dir.

    Raises:
        click.UsageError: If --quiet is passed without digest.output_dir.
    """
    app = require_app(ctx)
    output_dir = _resolve_output_dir(app)
    if quiet and output_dir is None:
        raise click.UsageError("--quiet requires digest.output_dir in config")
    markdown = asyncio.run(
        _run_digest(
            app,
            cutoff_at=_parse_cutoff_at(cutoff_at),
            top=top,
            output_dir=output_dir,
        )
    )
    if not quiet:
        click.echo(markdown)


def _resolve_output_dir(app: AppContext) -> Path | None:
    raw = app["settings"].digest.output_dir
    if raw is None:
        return None
    return Path(raw).expanduser()


async def _run_digest(
    app: AppContext,
    *,
    cutoff_at: datetime | None,
    top: int | None,
    output_dir: Path | None,
) -> str:
    return await run_with_store(
        app,
        lambda: app["digest_service"].run(
            cutoff_at=cutoff_at,
            top=top,
            output_dir=output_dir,
        ),
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
