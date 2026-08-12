"""Click commands for legacy SQLite migration."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, cast

import click

from jobfeed.adapters.migration._baseline_evidence import (
    validate_evidence_bundle,
    validate_restore_attestations,
)
from jobfeed.adapters.migration._baseline_workload import artifact_sha256
from jobfeed.adapters.migration.pg_baseline import PgDumpEvidence, capture_pg_baseline
from jobfeed.adapters.migration.pg_preprovisioned_restore import (
    RESTORE_POST_INSPECTION_PATH,
    SCRATCH_RESTORE_DSN,
    SOURCE_RESTORE_DSN,
    PreprovisionedRestoreConfig,
    ProvenanceVerification,
    capture_preprovisioned_restore,
    load_restore_bootstrap,
    verify_preprovisioned_provenance,
)
from jobfeed.adapters.store.legacy_import import ImportReport, import_legacy_sqlite
from jobfeed.adapters.store.parity import verify_import_parity
from jobfeed.adapters.store.postgres import PostgresStore
from jobfeed.cli import require_app

_RESTORE_PRE_INSPECTION_PATH = Path("/run/jobfeed-migration/pre-inspection.json")


def _wait_for_restore_bootstrap() -> Any:
    for _ in range(240):
        try:
            return load_restore_bootstrap()
        except FileNotFoundError:
            time.sleep(0.25)
    raise ValueError("restore bootstrap marker timed out")


def _wait_for_restore_file(path: Path, name: str) -> None:
    for _ in range(240):
        if path.is_file():
            return
        time.sleep(0.25)
    raise ValueError(f"{name} marker timed out")


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


def _write_new_json(path: Path, document: object) -> None:
    """Atomically create one canonical JSON artifact without overwriting.

    Args:
        path: Explicit artifact destination.
        document: JSON-serializable artifact.

    Raises:
        click.ClickException: If the output exists or cannot be created.
    """
    if path.exists():
        raise click.ClickException(f"Refusing to overwrite artifact: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (
        json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        + "\n"
    )
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=path.parent, delete=False
        ) as handle:
            handle.write(payload)
            temporary = Path(handle.name)
        temporary.replace(path)
    except OSError as exc:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        raise click.ClickException(f"Cannot write artifact {path}: {exc}") from exc


def _file_sha256(path: Path) -> str:
    """Hash one evidence artifact without loading it into memory.

    Args:
        path: Artifact to read.

    Returns:
        Lowercase SHA-256 digest.
    """
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@migrate.command(name="benchmark-store", help="Capture a versioned store benchmark.")
@click.option("--backend", required=True, type=click.Choice(["postgres", "sqlite"]))
@click.option("--dsn-env", help="Environment variable containing source DSN.")
@click.option(
    "--scratch-dsn-env", help="Environment variable containing disposable clone DSN."
)
@click.option(
    "--machine-token-env",
    required=True,
    help="Environment variable containing the shared benchmark machine token.",
)
@click.option(
    "--workload",
    required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
)
@click.option("--artifact-dir", required=True, type=click.Path(path_type=Path))
@click.option(
    "--source-dump",
    required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Read-only pg_dump artifact restored into this rehearsal database.",
)
@click.option(
    "--source-restore-attestation",
    required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
)
@click.option(
    "--scratch-restore-attestation",
    required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
)
def benchmark_store_command(**options: object) -> None:
    """Capture an atomic evidence bundle without writing to the source.

    Args:
        **options: Click-parsed backend, DSN names, workload, dump, and output.

    Raises:
        click.ClickException: If gates fail or an artifact cannot be created.
    """
    backend = cast(str, options["backend"])
    dsn_env = cast(str | None, options["dsn_env"])
    scratch_dsn_env = cast(str | None, options["scratch_dsn_env"])
    machine_token_env = cast(str, options["machine_token_env"])
    workload = cast(Path, options["workload"])
    artifact_dir = cast(Path, options["artifact_dir"])
    source_dump = cast(Path, options["source_dump"])
    source_attestation_path = cast(Path, options["source_restore_attestation"])
    scratch_attestation_path = cast(Path, options["scratch_restore_attestation"])
    if backend != "postgres":
        raise click.ClickException("SQLite benchmark implementation is not available")
    if not dsn_env or not scratch_dsn_env:
        raise click.ClickException(
            "Postgres benchmark requires both named DSN env vars"
        )
    dsn = os.environ.get(dsn_env)
    scratch_dsn = os.environ.get(scratch_dsn_env)
    machine_token = os.environ.get(machine_token_env)
    if not dsn or not scratch_dsn or not machine_token:
        raise click.ClickException(
            "Postgres benchmark DSN or machine environment is empty"
        )
    if dsn == scratch_dsn:
        raise click.ClickException("Source and contention scratch DSNs must differ")
    if artifact_dir.exists():
        raise click.ClickException(
            f"Refusing to overwrite artifact bundle: {artifact_dir}"
        )
    artifact_dir.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(
        tempfile.mkdtemp(
            prefix=f".{artifact_dir.name}.partial-", dir=artifact_dir.parent
        )
    )
    try:
        workload_document = json.loads(workload.read_text("utf-8"))
        git_commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        dump_sha256 = _file_sha256(source_dump)
        restore_attestations = validate_restore_attestations(
            json.loads(source_attestation_path.read_text("utf-8")),
            json.loads(scratch_attestation_path.read_text("utf-8")),
            dump_sha256=dump_sha256,
        )
        manifest, benchmark = capture_pg_baseline(
            dsn,
            scratch_dsn,
            workload_document,
            source=PgDumpEvidence(
                git_commit=git_commit,
                sha256=dump_sha256,
                size_bytes=source_dump.stat().st_size,
                restore_attestations=restore_attestations,
                machine_token=machine_token,
            ),
        )
        evidence_index = {
            "evidence_version": 1,
            "source_dump_sha256": dump_sha256,
            "manifest_sha256": artifact_sha256(manifest),
            "benchmark_sha256": artifact_sha256(benchmark),
            "workload_sha256": artifact_sha256(workload_document),
            "git_commit": git_commit,
        }
        validate_evidence_bundle(manifest, benchmark, evidence_index)
        _write_new_json(staging / "snapshot-manifest.json", manifest)
        _write_new_json(staging / "store-benchmark.json", benchmark)
        _write_new_json(staging / "evidence-index.json", evidence_index)
        if artifact_dir.exists():
            raise FileExistsError(
                f"artifact bundle appeared concurrently: {artifact_dir}"
            )
        staging.rename(artifact_dir)
    except Exception as exc:
        shutil.rmtree(staging, ignore_errors=True)
        if isinstance(exc, click.ClickException):
            raise
        raise click.ClickException(str(exc)) from exc
    click.echo(
        f"Evidence bundle: {artifact_dir} "
        f"manifest_sha256={evidence_index['manifest_sha256']} "
        f"benchmark_sha256={evidence_index['benchmark_sha256']}"
    )


@migrate.command(name="_capture-preprovisioned-baseline", hidden=True)
@click.option(
    "--machine-token-env",
    required=True,
    help="Environment variable containing the shared benchmark machine token.",
)
@click.option(
    "--workload",
    required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
)
@click.option("--artifact-dir", required=True, type=click.Path(path_type=Path))
def capture_preprovisioned_baseline_command(
    machine_token_env: str, workload: Path, artifact_dir: Path
) -> None:
    """Run the gated internal half of the public host migration command.

    Args:
        machine_token_env: Fixed environment variable containing the host token.
        workload: Frozen backend-neutral benchmark workload document.
        artifact_dir: New output bundle directory inside the artifact mount.

    Raises:
        click.ClickException: If any restore, capture, or provenance gate fails.
    """
    try:
        machine_token = os.environ.get(machine_token_env)
        if not machine_token:
            raise ValueError("benchmark machine token environment is empty")
        bootstrap = _wait_for_restore_bootstrap()
        config = PreprovisionedRestoreConfig(
            dump_path=Path("/run/jobfeed-migration/source.dump"),
            project_root=Path("/app"),
            alembic_executable=Path("/app/.venv/bin/alembic"),
            source_dsn=SOURCE_RESTORE_DSN,
            scratch_dsn=SCRATCH_RESTORE_DSN,
            expected_project_label=bootstrap.project_label,
            bootstrap=bootstrap,
        )
        workload_document = json.loads(workload.read_text("utf-8"))

        def capture(result: object) -> dict[str, object]:
            restore = cast(Any, result)
            git_commit = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            manifest, benchmark = capture_pg_baseline(
                restore.source_dsn,
                restore.scratch_dsn,
                workload_document,
                source=PgDumpEvidence(
                    git_commit=git_commit,
                    sha256=restore.dump_sha256,
                    size_bytes=restore.dump_size_bytes,
                    restore_attestations=restore.attestations,
                    machine_token=machine_token,
                ),
            )
            index = {
                "evidence_version": 1,
                "source_dump_sha256": restore.dump_sha256,
                "manifest_sha256": artifact_sha256(manifest),
                "benchmark_sha256": artifact_sha256(benchmark),
                "workload_sha256": artifact_sha256(workload_document),
                "git_commit": git_commit,
            }
            validate_evidence_bundle(manifest, benchmark, index)
            if artifact_dir.exists():
                raise FileExistsError(f"artifact bundle exists: {artifact_dir}")
            staging = Path(
                tempfile.mkdtemp(
                    prefix=f".{artifact_dir.name}.partial-", dir=artifact_dir.parent
                )
            )
            try:
                _write_new_json(staging / "snapshot-manifest.json", manifest)
                _write_new_json(staging / "store-benchmark.json", benchmark)
                _write_new_json(staging / "evidence-index.json", index)
                staging.rename(artifact_dir)
            except Exception:
                shutil.rmtree(staging, ignore_errors=True)
                raise
            return {"manifest": manifest, "benchmark": benchmark, "index": index}

        ready = capture_preprovisioned_restore(
            config,
            capture,
            evidence_bundle_sha256=artifact_sha256,
        )
        _wait_for_restore_file(RESTORE_POST_INSPECTION_PATH, "post-inspection")
        pre_docs = json.loads(_RESTORE_PRE_INSPECTION_PATH.read_text("utf-8"))
        post_docs = json.loads(RESTORE_POST_INSPECTION_PATH.read_text("utf-8"))
        bundle = {
            "manifest": json.loads(
                (artifact_dir / "snapshot-manifest.json").read_text("utf-8")
            ),
            "benchmark": json.loads(
                (artifact_dir / "store-benchmark.json").read_text("utf-8")
            ),
            "index": json.loads(
                (artifact_dir / "evidence-index.json").read_text("utf-8")
            ),
        }
        verify_preprovisioned_provenance(
            ProvenanceVerification(
                bootstrap=bootstrap,
                pre_docs=pre_docs,
                post_docs=post_docs,
                capture_ready=ready,
                actual_evidence_bundle_sha256=artifact_sha256(bundle),
            )
        )
    except Exception as exc:
        if isinstance(exc, click.ClickException):
            raise
        raise click.ClickException(str(exc)) from exc


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
    "--verify/--no-verify",
    "verify",
    default=True,
    help="Run parity verification after import (default: on; --no-verify skips).",
)
@click.option(
    "--manifest",
    "manifest_path",
    type=click.Path(exists=True, path_type=Path),
    default=None,
    help="Manifest JSON of expected counts. Default: derive from the source DB.",
)
@click.pass_context
def import_sqlite(
    ctx: click.Context,
    from_path: Path,
    dry_run: bool,
    verify: bool,
    manifest_path: Path | None,
) -> None:
    """Import a legacy SQLite v16 database into a fresh PostgreSQL store.

    Uses asyncio.run() to bridge the async store operations into the
    synchronous Click command handler. The target store is taken from the
    configured app context (--config or defaults).

    Args:
        ctx: Click invocation context.
        from_path: Path to the legacy v16 .db file.
        dry_run: If True, print plan without writing.
        verify: If True (default), run parity checks after import; --no-verify
            skips them.
        manifest_path: Optional manifest JSON; when omitted, expected counts are
            derived from the source database.
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


def _get_target_store(ctx: click.Context) -> PostgresStore:
    """Get the target store from the Click app context.

    Args:
        ctx: Click invocation context.

    Returns:
        The configured PostgreSQL migration target store, which implements the
        bulk-import and parity-read operations the migration pipeline relies on.

    Raises:
        click.ClickException: If the app context is not initialized or the
            configured store does not support legacy migration.
    """
    app = require_app(ctx)
    store = app["store"]
    if isinstance(store, PostgresStore):
        return store
    raise click.ClickException(
        f"Unsupported migration target store: {type(store).__name__}"
    )


def _run_verify(
    from_path: Path, store: PostgresStore, manifest_path: Path | None
) -> None:
    """Run parity verification and print results.

    Args:
        from_path: Path to the legacy v16 .db file.
        store: Target store to verify against.
        manifest_path: Optional explicit manifest path.

    Raises:
        click.ClickException: If parity verification fails.
    """
    manifest = _resolve_manifest(from_path, manifest_path)
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


def _resolve_manifest(from_path: Path, manifest_path: Path | None) -> dict[str, Any]:
    """Resolve the parity manifest: explicit file, else derive from the source.

    Args:
        from_path: Legacy v16 source database.
        manifest_path: Optional explicit manifest JSON path.

    Returns:
        Parity manifest dict (expected per-table row counts).
    """
    if manifest_path is not None:
        return _load_manifest(manifest_path)
    return _derive_manifest(from_path)


def _derive_manifest(from_path: Path) -> dict[str, Any]:
    """Derive expected row counts from the source database for parity checks.

    Avoids depending on a checked-in test fixture: the legacy source is the
    authority for what should land in the target after a 1:1 import.

    Args:
        from_path: Legacy v16 source database.

    Returns:
        Manifest dict with schema_version and per-table row counts.
    """
    conn = _open_legacy_readonly(from_path)
    try:
        version = _read_schema_version(conn)
        counts = _read_table_counts(conn)
    finally:
        conn.close()
    return {
        "schema_version": int(version) if version is not None else None,
        "tables": {
            table: {"row_count": count} for table, count in counts.items() if count >= 0
        },
    }


def _load_manifest(manifest_path: Path) -> dict[str, Any]:
    """Load an explicit manifest JSON for parity verification.

    Args:
        manifest_path: Path to a manifest JSON file.

    Returns:
        Parsed manifest dict.

    Raises:
        click.ClickException: If the manifest file cannot be loaded.
    """
    if not manifest_path.exists():
        raise click.ClickException(f"Manifest not found: {manifest_path}")

    try:
        return json.loads(manifest_path.read_text("utf-8"))  # type: ignore[no-any-return]
    except (json.JSONDecodeError, OSError) as exc:
        raise click.ClickException(f"Cannot load manifest: {exc}") from exc


async def _do_import(from_path: Path, store: PostgresStore) -> ImportReport:
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
    from_path: Path, store: PostgresStore, manifest: dict[str, Any]
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
