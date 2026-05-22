"""Click commands for legacy SQLite migration."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path
from typing import Any

import click

from jobfeed.adapters.store.legacy_import import ImportReport, import_legacy_sqlite
from jobfeed.adapters.store.parity import verify_import_parity
from jobfeed.adapters.store.sqlite import SQLiteStore
from jobfeed.cli import require_app

# ---- Helpers ----


def _open_legacy_readonly(path: Path) -> sqlite3.Connection:
    """Open a legacy SQLite database in read-only mode.

    Args:
        path: Path to the legacy .db file.

    Returns:
        Read-only sqlite3 connection.

    Raises:
        click.ClickException: If the path does not exist or is not a valid DB.
    """
    if not path.exists():
        raise click.ClickException(f"Legacy database not found: {path}")
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        return conn
    except sqlite3.Error as exc:
        raise click.ClickException(f"Cannot open legacy database: {exc}") from exc


def _read_schema_version(conn: sqlite3.Connection) -> str | None:
    """Read schema_version from the legacy state table.

    Args:
        conn: Read-only connection to the legacy database.

    Returns:
        Schema version string, or None if not found.
    """
    try:
        cursor = conn.execute("SELECT value FROM state WHERE key = 'schema_version'")
        row = cursor.fetchone()
        return dict(row)["value"] if row else None
    except sqlite3.Error:
        return None


def _read_table_counts(conn: sqlite3.Connection) -> dict[str, int]:
    """Read row counts for all known legacy tables.

    Args:
        conn: Read-only connection to the legacy database.

    Returns:
        Dict mapping table name to row count.
    """
    tables = [
        "jobs",
        "evaluations",
        "job_status",
        "job_status_history",
        "applied",
        "resume_snapshots",
        "resume_variants",
        "companies",
        "cost_ledger",
        "state",
    ]
    counts: dict[str, int] = {}
    for table in tables:
        try:
            cursor = conn.execute(f"SELECT COUNT(*) FROM {table}")
            row = cursor.fetchone()
            counts[table] = row[0] if row else 0
        except sqlite3.Error:
            counts[table] = -1  # table missing
    return counts


def _check_fk_health(conn: sqlite3.Connection) -> list[str]:
    """Run basic FK integrity checks on the legacy database.

    Args:
        conn: Read-only connection to the legacy database.

    Returns:
        List of warning strings (empty if healthy).
    """
    warnings: list[str] = []
    try:
        conn.row_factory = sqlite3.Row
        job_cursor = conn.execute("SELECT id FROM jobs")
        job_ids = {dict(r)["id"] for r in job_cursor.fetchall()}

        fk_tables = ["evaluations", "job_status", "job_status_history", "applied"]
        for table in fk_tables:
            warnings.extend(_check_fk_table(conn, table, job_ids))
    except sqlite3.Error as exc:
        warnings.append(f"FK check error: {exc}")
    return warnings


def _check_fk_table(
    conn: sqlite3.Connection, table: str, job_ids: set[int]
) -> list[str]:
    """Check FK integrity for a single table against known job IDs.

    Args:
        conn: Read-only connection to the legacy database.
        table: Table name containing job_id foreign keys.
        job_ids: Set of valid job IDs from the jobs table.

    Returns:
        List of warning strings for orphaned references.
    """
    try:
        cursor = conn.execute(f"SELECT job_id FROM {table}")
        rows = cursor.fetchall()
    except sqlite3.Error:
        return [f"{table}: table not accessible"]

    return [
        f"{table}.job_id={dict(row)['job_id']} has no matching job"
        for row in rows
        if dict(row)["job_id"] not in job_ids
    ]


# ---- Click commands ----


@click.group(name="migrate", help="Legacy database migration commands.")
def migrate() -> None:
    """Legacy database migration commands."""


@migrate.command(name="inspect-sqlite", help="Inspect a legacy SQLite v16 database.")
@click.argument("path", type=click.Path(exists=True, path_type=Path))
def inspect_sqlite(path: Path) -> None:
    """Read a legacy SQLite database and print schema version, row counts, and health.

    This command is read-only and never modifies the source database.

    Args:
        path: Path to the legacy v16 .db file.
    """
    conn = _open_legacy_readonly(path)
    try:
        version = _read_schema_version(conn)
        counts = _read_table_counts(conn)
        fk_warnings = _check_fk_health(conn)
    finally:
        conn.close()

    click.echo(f"Schema version: {version or 'unknown'}")
    click.echo("")
    click.echo("Row counts:")
    for table, count in counts.items():
        status = str(count) if count >= 0 else "MISSING"
        click.echo(f"  {table:30s} {status}")

    total = sum(c for c in counts.values() if c >= 0)
    click.echo(f"  {'TOTAL':30s} {total}")

    click.echo("")
    if fk_warnings:
        click.echo(f"Health warnings ({len(fk_warnings)}):")
        for w in fk_warnings:
            click.echo(f"  - {w}")
    else:
        click.echo("Health: OK (no FK violations detected)")


@migrate.command(
    name="import-sqlite",
    help="Import a legacy SQLite v16 database into the configured store.",
)
@click.option(
    "--from",
    "from_path",
    required=True,
    type=click.Path(path_type=Path),
    help="Path to the legacy v16 .db file.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Print import plan without writing any data.",
)
@click.option(
    "--verify",
    is_flag=True,
    default=False,
    help="Run parity assertion harness after import.",
)
@click.option(
    "--manifest",
    "manifest_path",
    type=click.Path(exists=True, path_type=Path),
    default=None,
    help="Path to manifest JSON for --verify parity checks.",
)
@click.pass_context
def import_sqlite(
    ctx: click.Context,
    from_path: Path,
    dry_run: bool,
    verify: bool,
    manifest_path: Path | None,
) -> None:
    """Import a legacy SQLite v16 database into a fresh SQLiteStore.

    Uses asyncio.run() to bridge the async store operations into the
    synchronous Click command handler. The target store is taken from the
    configured app context (--config or defaults).

    Args:
        ctx: Click invocation context.
        from_path: Path to the legacy v16 .db file.
        dry_run: If True, print plan without writing.
        verify: If True, run parity checks after import.
        manifest_path: Optional path to manifest JSON for parity checks.
    """
    version, counts = _validate_legacy_source(from_path)

    if dry_run:
        _print_dry_run(from_path, version, counts)
        return

    store = _get_target_store(ctx)
    report = asyncio.run(_do_import(from_path, store))
    _print_import_report(report)

    if verify:
        _run_verify(from_path, store, manifest_path)


def _validate_legacy_source(from_path: Path) -> tuple[str, dict[str, int]]:
    """Validate and inspect the legacy source database.

    Args:
        from_path: Path to the legacy .db file.

    Returns:
        Tuple of (schema version string, table counts dict).

    Raises:
        click.ClickException: If the source is invalid.
    """
    if not from_path.exists():
        raise click.ClickException(f"Legacy database not found: {from_path}")

    conn = _open_legacy_readonly(from_path)
    try:
        version = _read_schema_version(conn)
        if version != "16":
            raise click.ClickException(
                f"Expected legacy schema_version=16, got {version or 'unknown'}"
            )
        counts = _read_table_counts(conn)
    finally:
        conn.close()
    return version, counts


def _print_dry_run(from_path: Path, version: str, counts: dict[str, int]) -> None:
    """Print dry-run import plan.

    Args:
        from_path: Source database path.
        version: Schema version string.
        counts: Table row counts.
    """
    click.echo("Dry run: import plan (no data will be written)")
    click.echo(f"Source: {from_path}")
    click.echo(f"Schema version: {version}")
    click.echo("")
    click.echo("Tables to import:")
    for table, count in counts.items():
        click.echo(f"  {table:30s} {count} rows")
    total = sum(c for c in counts.values() if c >= 0)
    click.echo(f"  {'TOTAL':30s} {total} rows")


def _print_import_report(report: ImportReport) -> None:
    """Print import results and raise on errors.

    Args:
        report: Completed import report.

    Raises:
        click.ClickException: If the import had errors.
    """
    click.echo("Import completed successfully.")
    click.echo(f"Duration: {report.duration_s:.2f}s")
    click.echo("")
    click.echo("Imported rows:")
    for table, count in report.tables_imported.items():
        click.echo(f"  {table:30s} {count}")

    if report.warnings:
        click.echo("")
        click.echo(f"Warnings ({len(report.warnings)}):")
        for w in report.warnings:
            click.echo(f"  - {w}")

    if report.errors:
        click.echo("")
        click.echo(f"Errors ({len(report.errors)}):")
        for e in report.errors:
            click.echo(f"  - {e}")
        raise click.ClickException("Import completed with errors")


def _get_target_store(ctx: click.Context) -> SQLiteStore:
    """Get the target store from the Click app context.

    Args:
        ctx: Click invocation context.

    Returns:
        SQLiteStore from the configured app context.

    Raises:
        click.ClickException: If the app context is not initialized.
    """
    app = require_app(ctx)
    return app["store"]


def _run_verify(
    from_path: Path, store: SQLiteStore, manifest_path: Path | None
) -> None:
    """Run parity verification and print results.

    Args:
        from_path: Path to the legacy v16 .db file.
        store: Target store to verify against.
        manifest_path: Optional explicit manifest path.

    Raises:
        click.ClickException: If parity verification fails.
    """
    manifest = _load_manifest(manifest_path)
    parity_report = asyncio.run(_do_verify(from_path, store, manifest))

    click.echo("")
    click.echo("Parity verification:")
    for check in parity_report.checks:
        icon = "PASS" if check.passed else "FAIL"
        click.echo(f"  [{icon}] {check.name}: {check.details}")

    if not parity_report.passed:
        raise click.ClickException("Parity verification failed")
    click.echo("")
    click.echo("All parity checks passed.")


def _load_manifest(manifest_path: Path | None) -> dict[str, Any]:
    """Load the manifest JSON for parity verification.

    Args:
        manifest_path: Explicit path, or None to use the default fixture.

    Returns:
        Parsed manifest dict.

    Raises:
        click.ClickException: If the manifest file cannot be loaded.
    """
    if manifest_path is None:
        # Default to the test fixture manifest
        default = (
            Path(__file__).resolve().parent.parent.parent.parent
            / "tests"
            / "fixtures"
            / "legacy_v16_manifest.json"
        )
        manifest_path = default

    if not manifest_path.exists():
        raise click.ClickException(f"Manifest not found: {manifest_path}")

    try:
        return json.loads(manifest_path.read_text("utf-8"))  # type: ignore[no-any-return]
    except (json.JSONDecodeError, OSError) as exc:
        raise click.ClickException(f"Cannot load manifest: {exc}") from exc


async def _do_import(from_path: Path, store: SQLiteStore) -> ImportReport:
    """Run the async import pipeline.

    Args:
        from_path: Path to the legacy v16 .db file.
        store: Target store to import into.

    Returns:
        ImportReport with per-table counts.
    """
    await store.connect()
    try:
        return await import_legacy_sqlite(from_path, store)
    finally:
        await store.close()


async def _do_verify(
    from_path: Path, store: SQLiteStore, manifest: dict[str, Any]
) -> Any:
    """Run the async parity verification.

    Args:
        from_path: Path to the legacy v16 .db file.
        store: Target store to verify against.
        manifest: Parsed manifest dict.

    Returns:
        ParityReport with all check results.
    """
    await store.connect()
    try:
        return await verify_import_parity(from_path, store, manifest)
    finally:
        await store.close()


__all__ = ["migrate"]
