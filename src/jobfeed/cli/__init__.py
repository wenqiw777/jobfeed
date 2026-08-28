"""Command-line interface for Jobfeed."""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import NotRequired, TypedDict, TypeVar, cast

import click

from jobfeed.adapters.sources.mock import MockSource
from jobfeed.adapters.store.sqlite import SQLiteStore
from jobfeed.cli._probe import ProbeVendorFn, build_probe_company
from jobfeed.config import Settings, load_settings
from jobfeed.observability import (
    JobfeedLogger,
    configure_logging,
    get_logger,
    init_otel,
    init_sentry,
)
from jobfeed.onboarding_secrets import ProviderSecretStore
from jobfeed.ports.source import SimpleSource
from jobfeed.ports.store import JobStore
from jobfeed.ports.store_ext import StageBThresholdSync
from jobfeed.services.digest import DigestService, DigestStore
from jobfeed.services.run_orchestration import RunLeaseOrchestrator
from jobfeed.services.scan import ScanService

T = TypeVar("T")


class AppContext(TypedDict):
    """Runtime dependency graph shared by Click commands."""

    settings: Settings
    config_path: Path | None
    store: JobStore
    sources: dict[str, SimpleSource]
    scan_service: ScanService
    run_orchestrator: NotRequired[RunLeaseOrchestrator]
    stage_b_threshold_sync: NotRequired[StageBThresholdSync]
    digest_service: DigestService
    probe_company: ProbeVendorFn
    logger: JobfeedLogger
    verbose: bool
    provider_secrets: NotRequired[ProviderSecretStore]


def create_app(config_path: Path | None = None) -> AppContext:
    """Build the application dependency graph.

    Wires the shared persistent SQLite store from ``settings.db.path``.

    The evaluate command builds its own EvaluateService lazily so that
    scan and digest work without LLM CLI tools installed.

    Args:
        config_path: Optional path to the TOML configuration file.

    Returns:
        App context containing settings, adapters, services, and logger.

    Raises:
        FileNotFoundError: If an explicit config path does not exist.
    """
    settings = load_settings(config_path)
    configure_logging(
        settings.observability.log_level,
        settings.observability.log_format,
        otel_enabled=settings.observability.otel_enabled,
    )
    init_otel(settings.observability)
    init_sentry(settings.observability)
    logger = get_logger()
    store = _create_store(settings)
    project_root = config_path.resolve().parent if config_path else Path.cwd()
    provider_secrets = ProviderSecretStore(project_root / "data" / "secrets.toml")
    run_orchestrator = RunLeaseOrchestrator(store)
    sources: dict[str, SimpleSource] = {"mock": MockSource()}
    return AppContext(
        settings=settings,
        config_path=config_path,
        store=store,
        sources=sources,
        scan_service=ScanService(
            store,
            logger,
            run_orchestrator,
        ),
        run_orchestrator=run_orchestrator,
        stage_b_threshold_sync=store,
        digest_service=DigestService(cast(DigestStore, store), logger),
        probe_company=build_probe_company(settings),
        logger=logger,
        verbose=False,
        provider_secrets=provider_secrets,
    )


def require_app(ctx: click.Context) -> AppContext:
    """Return the initialized app context from Click.

    Args:
        ctx: Click invocation context.

    Returns:
        Initialized application context.

    Raises:
        click.ClickException: If the command is invoked without app context.
    """
    if ctx.obj is None:
        raise click.ClickException("Jobfeed CLI context is not initialized")
    return cast(AppContext, ctx.obj)


def require_enabled(enabled: bool, source_name: str) -> None:
    """Raise a ClickException when a requested source is disabled in config.

    Shared guard for every command that targets a specific source — the scan
    builders and the enrich commands alike — so an explicitly requested but
    disabled source always fails the same way.

    Args:
        enabled: The source config's ``enabled`` flag.
        source_name: CLI token used in the error message.

    Raises:
        click.ClickException: If the source is disabled.
    """
    if not enabled:
        raise click.ClickException(f"{source_name} source is disabled in config")


async def run_with_store(
    app: AppContext,
    action: Callable[[], Awaitable[T]],
) -> T:
    """Run an async command action with an opened store connection.

    Args:
        app: Initialized application context.
        action: Async command action that needs the store connection.

    Returns:
        Result returned by `action`.

    Raises:
        click.ClickException: If the store lifecycle or command action fails.

    Notes:
        The CLI owns connection lifecycle so services stay independent from
        process and command boundaries.
    """
    store = app["store"]
    try:
        await store.connect()
        try:
            return await action()
        finally:
            await store.close()
    except click.ClickException:
        raise
    except Exception as exc:
        raise click.ClickException(str(exc)) from exc


@click.group(
    name="jobfeed",
    help="Run Jobfeed command-line workflows.",
    invoke_without_command=True,
    no_args_is_help=False,
)
@click.option(
    "--config",
    "config_path",
    type=click.Path(dir_okay=False, path_type=Path),
    help="Path to a TOML config file.",
)
@click.option("--verbose", is_flag=True, help="Enable verbose command output.")
@click.pass_context
def cli(ctx: click.Context, config_path: Path | None, verbose: bool) -> None:
    """Run Jobfeed command-line workflows.

    Args:
        ctx: Click invocation context.
        config_path: Optional path to the TOML configuration file.
        verbose: Whether verbose command output was requested.

    Side effects:
        Stores the initialized dependency graph on the Click context for
        subcommands.

    Raises:
        click.ClickException: If configuration cannot build a Phase 0 app.
    """
    config_path = _resolve_config_path(config_path)
    try:
        app = create_app(config_path)
    except (FileNotFoundError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    if verbose:
        _enable_verbose_logging(app)
    app["verbose"] = verbose
    ctx.obj = app
    if ctx.invoked_subcommand is None:
        # Keep the installed Python entrypoint consistent with bin/jobfeed.
        ctx.invoke(serve, port=_default_serve_port(DEFAULT_PORT))


def _default_serve_port(default: int) -> int:
    """Read the wrapper-compatible port override for a bare invocation.

    Args:
        default: Port used when ``JOBFEED_PORT`` is unset.

    Returns:
        The selected foreground server port.

    Raises:
        click.ClickException: If ``JOBFEED_PORT`` is not an integer.
    """
    raw_port = os.environ.get("JOBFEED_PORT")
    if raw_port is None:
        return default
    try:
        return int(raw_port)
    except ValueError as exc:
        raise click.ClickException("JOBFEED_PORT must be an integer") from exc


def _enable_verbose_logging(app: AppContext) -> None:
    settings = app["settings"]
    configure_logging("debug", settings.observability.log_format)
    app["logger"].debug("cli_verbose_enabled")


_DEFAULT_CONFIG_FILENAME = "config.toml"


def _resolve_config_path(config_path: Path | None) -> Path | None:
    """Resolve the effective config path for a CLI invocation.

    An explicit ``--config`` always wins. When it is omitted, fall back to a
    ``config.toml`` in the current working directory if one exists, so
    ``jobfeed <cmd>`` uses a project-local config without repeating ``--config``
    every time. Returns None when neither is present (built-in defaults apply).

    Args:
        config_path: The explicit ``--config`` path, or None.

    Returns:
        The explicit path, the discovered ``./config.toml``, or None.
    """
    if config_path is not None:
        return config_path
    cwd_config = Path(_DEFAULT_CONFIG_FILENAME)
    if cwd_config.is_file():
        click.echo(f"Using config: {cwd_config}", err=True)
        return cwd_config
    return None


def _create_store(settings: Settings) -> SQLiteStore:
    return SQLiteStore(settings.db.path)


from jobfeed.cli.apply import apply_cmd, apply_history  # noqa: E402
from jobfeed.cli.bootstrap import bootstrap_companies  # noqa: E402
from jobfeed.cli.companies import companies  # noqa: E402
from jobfeed.cli.dev import dev  # noqa: E402
from jobfeed.cli.digest import digest  # noqa: E402
from jobfeed.cli.enrich import enrich_linkedin_guest, enrich_paste  # noqa: E402
from jobfeed.cli.evaluate import evaluate  # noqa: E402
from jobfeed.cli.interview import interview  # noqa: E402
from jobfeed.cli.login import login  # noqa: E402
from jobfeed.cli.maintenance import mark_stale_closed  # noqa: E402
from jobfeed.cli.ml_gate import ml_gate  # noqa: E402
from jobfeed.cli.scan import scan  # noqa: E402
from jobfeed.cli.serve import DEFAULT_PORT, serve  # noqa: E402
from jobfeed.cli.snapshots import snapshots  # noqa: E402
from jobfeed.cli.status import archive, followup, mark, note  # noqa: E402
from jobfeed.cli.status_query import list_cmd, stats  # noqa: E402

cli.add_command(scan)
cli.add_command(dev)
cli.add_command(evaluate)
cli.add_command(digest)
cli.add_command(login)
cli.add_command(mark_stale_closed)
cli.add_command(ml_gate)
cli.add_command(mark)
cli.add_command(archive)
cli.add_command(note)
cli.add_command(followup)
cli.add_command(list_cmd)
cli.add_command(stats)
cli.add_command(apply_cmd)
cli.add_command(apply_history)
cli.add_command(snapshots)
cli.add_command(enrich_paste)
cli.add_command(enrich_linkedin_guest)
cli.add_command(interview)
cli.add_command(companies)
cli.add_command(bootstrap_companies)
cli.add_command(serve)


__all__ = [
    "AppContext",
    "ProbeVendorFn",
    "cli",
    "create_app",
    "require_app",
    "require_enabled",
    "run_with_store",
]
