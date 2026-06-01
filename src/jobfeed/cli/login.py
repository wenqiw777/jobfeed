"""Click command for one-time source login flows."""

from __future__ import annotations

import asyncio
from pathlib import Path

import click

from jobfeed.adapters.sources._linkedin_dom import LINKEDIN_LOGIN
from jobfeed.adapters.sources.linkedin import _persistent_context
from jobfeed.cli import AppContext, require_app
from jobfeed.config import SourcesLinkedInConfig


@click.group(name="login", help="Open one-time source login workflows.")
def login() -> None:
    """Run one-time login workflows for authenticated sources."""


@login.command(name="linkedin", help="Open a headed LinkedIn login browser.")
@click.pass_context
def login_linkedin(ctx: click.Context) -> None:
    """Open LinkedIn with the configured persistent profile.

    Args:
        ctx: Click invocation context.

    Raises:
        click.ClickException: If the login browser fails to launch (for example
            when Chromium is not installed).
    """
    app = require_app(ctx)
    try:
        asyncio.run(_run_linkedin_login(app))
    except click.ClickException:
        raise
    except Exception as exc:
        # A missing Chromium (or any Playwright launch failure) should read as a
        # clean CLI error, not a raw traceback.
        raise click.ClickException(
            f"LinkedIn login failed: {exc}. Install the browser with "
            "'playwright install chromium' and retry."
        ) from exc


async def _run_linkedin_login(app: AppContext) -> None:
    config = app["settings"].sources.linkedin
    await open_linkedin_login_browser(config)
    click.echo("LinkedIn login browser closed")


async def open_linkedin_login_browser(config: SourcesLinkedInConfig) -> None:
    """Open a headed browser using the configured LinkedIn profile directory.

    Args:
        config: LinkedIn source configuration.
    """
    profile_dir = Path(config.profile_dir).expanduser()
    async with _persistent_context(
        profile_dir=profile_dir,
        headless=False,
    ) as context:
        page = await context.new_page()
        await page.goto(LINKEDIN_LOGIN, wait_until="domcontentloaded")
        click.echo("Complete LinkedIn login in the browser, then press Enter.")
        await asyncio.to_thread(input, "Press Enter after LinkedIn is logged in: ")


__all__ = ["login", "open_linkedin_login_browser"]
