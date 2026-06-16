"""Click command serving the web API with uvicorn, loopback only."""

from __future__ import annotations

import click
import uvicorn

from jobfeed.cli import require_app

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 7654
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


@click.command(name="serve", help="Serve the Jobfeed web API on loopback.")
@click.option(
    "--host",
    default=DEFAULT_HOST,
    show_default=True,
    help="Bind address; loopback only (127.0.0.1, ::1, localhost).",
)
@click.option(
    "--port",
    default=DEFAULT_PORT,
    show_default=True,
    type=int,
    help="TCP port to listen on.",
)
@click.pass_context
def serve(ctx: click.Context, host: str, port: int) -> None:
    """Serve the web API until interrupted.

    Args:
        ctx: Click invocation context.
        host: Bind address; must be a loopback address.
        port: TCP port to listen on.

    Raises:
        click.ClickException: If ``host`` is not a loopback address.
    """
    app = require_app(ctx)
    _require_loopback(host)
    # Imported lazily: jobfeed.web.app imports jobfeed.cli for the shared
    # create_app assembly, so a module-level import here would be circular.
    from jobfeed.web.app import build_web_app  # noqa: PLC0415

    web_app = build_web_app(app)
    if not web_app.state.is_spa_mounted:
        click.echo(
            "Web UI not built (web-ui/dist missing); serving the API only. "
            "Run `make web-build` once to serve the UI.",
            err=True,
        )
    # Request lines come from the web layer's structlog middleware; uvicorn's
    # own access log would duplicate them.
    uvicorn.run(web_app, host=host, port=port, access_log=False)


def _require_loopback(host: str) -> None:
    """Reject any bind address outside the loopback allowlist.

    Args:
        host: Requested bind address.

    Raises:
        click.ClickException: If the host is not a loopback address.
    """
    if host in LOOPBACK_HOSTS:
        return
    allowed = ", ".join(sorted(LOOPBACK_HOSTS))
    raise click.ClickException(
        f"serve binds loopback only ({allowed}); refusing host {host!r}. "
        "Network mode with authentication is future work."
    )


__all__ = ["serve"]
