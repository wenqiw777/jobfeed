"""Click command for the manual JD paste fallback: enrich-paste."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import cast

import click

from jobfeed.cli import AppContext, require_app, run_with_store
from jobfeed.domain.quality import assess_quality
from jobfeed.ports.store_ops import StoreOpsMixin

_PLATFORM_CHOICES = ("linkedin", "indeed")


@click.command(name="enrich-paste", help="Paste a JD manually for a stub job.")
@click.argument("canonical_id")
@click.option(
    "--platform",
    type=click.Choice(_PLATFORM_CHOICES),
    default="linkedin",
    show_default=True,
    help="Source platform of the job.",
)
@click.option(
    "--from-file",
    "from_file",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help=(
        "Read the JD from a host file (when running via ./bin/jobfeed, "
        "pipe the file to stdin instead)."
    ),
)
@click.pass_context
def enrich_paste(
    ctx: click.Context,
    canonical_id: str,
    platform: str,
    from_file: Path | None,
) -> None:
    """Store manually pasted JD text for an existing job.

    Args:
        ctx: Click invocation context.
        canonical_id: Platform-specific job identity.
        platform: Source platform of the job.
        from_file: Optional file to read the JD text from (default: stdin).

    Raises:
        click.UsageError: If the JD text is empty or whitespace-only.
    """
    app = require_app(ctx)
    jd_text = _load_jd_text(from_file)
    if not jd_text.strip():
        raise click.UsageError("JD text is empty")
    asyncio.run(
        _run_enrich_paste(
            app,
            platform=platform,
            canonical_id=canonical_id,
            jd_text=jd_text,
        )
    )


def _load_jd_text(from_file: Path | None) -> str:
    """Read JD text from a file when given, else from stdin.

    Args:
        from_file: Optional path to a JD text file.

    Returns:
        Raw JD text.

    Raises:
        click.ClickException: If the file cannot be read.
    """
    if from_file is None:
        return click.get_text_stream("stdin").read()
    try:
        return from_file.read_text(encoding="utf-8")
    except OSError as exc:
        raise click.ClickException(f"cannot read {from_file}: {exc}") from exc


async def _run_enrich_paste(
    app: AppContext,
    *,
    platform: str,
    canonical_id: str,
    jd_text: str,
) -> None:
    async def action() -> None:
        # An unknown canonical_id raises ValueError in the store, which
        # run_with_store surfaces as a clean nonzero ClickException.
        store = cast(StoreOpsMixin, app["store"])
        job_id = await store.enrich_paste(
            platform=platform,
            canonical_id=canonical_id,
            jd_text=jd_text,
        )
        quality = assess_quality(jd_text)
        click.echo(f"Enriched job {job_id} (quality: {quality})")

    await run_with_store(app, action)


__all__ = ["enrich_paste"]
