"""Click commands for legacy SQLite migration."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sqlite3
import tempfile
import time
from pathlib import Path
from typing import Any, cast

import click

from jobfeed.adapters.migration._baseline_evidence import (
    validate_evidence_bundle,
)
from jobfeed.adapters.migration._baseline_workload import artifact_sha256
from jobfeed.adapters.migration.baseline_provenance import (
    build_provenance_index,
    validate_provenance_bundle,
)
from jobfeed.adapters.migration.pg_baseline import PgDumpEvidence, capture_pg_baseline
from jobfeed.adapters.migration.pg_preprovisioned_restore import (
    RESTORE_BOOTSTRAP_PATH,
    RESTORE_CAPTURE_READY_PATH,
    RESTORE_POST_INSPECTION_PATH,
    RESTORE_VERIFIED_PATH,
    SCRATCH_RESTORE_DSN,
    SOURCE_RESTORE_DSN,
    PreprovisionedRestoreConfig,
    ProvenanceVerification,
    capture_preprovisioned_restore,
    load_restore_bootstrap,
    verify_preprovisioned_provenance,
)
from jobfeed.adapters.migration.sqlite_cutover_rehearsal import (
    CutoverSourceEvidence,
    run_cutover_rehearsal,
)
from jobfeed.adapters.store.legacy_import import ImportReport, import_legacy_sqlite
from jobfeed.adapters.store.parity import verify_import_parity
from jobfeed.adapters.store.postgres import PostgresStore
from jobfeed.cli import require_app

_RESTORE_PRE_INSPECTION_PATH = Path("/run/jobfeed-migration/input/pre-inspection.json")
_FORMAL_RESOURCE_FINGERPRINT_PATH = Path(
    "/run/jobfeed-migration/input/formal-resource-fingerprints.json"
)
_GIT_COMMIT_LENGTH = 40


def _migration_timeout_seconds() -> float:
    raw = os.environ.get("JOBFEED_MIGRATION_TIMEOUT_SECONDS", "1800")
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError("migration timeout must be a positive number") from exc
    if value <= 0:
        raise ValueError("migration timeout must be a positive number")
    return value


def _wait_for_restore_bootstrap() -> Any:
    deadline = time.monotonic() + _migration_timeout_seconds()
    while time.monotonic() < deadline:
        try:
            return load_restore_bootstrap()
        except FileNotFoundError:
            time.sleep(0.25)
    raise ValueError("restore bootstrap marker timed out")


def _wait_for_restore_file(path: Path, name: str) -> None:
    deadline = time.monotonic() + _migration_timeout_seconds()
    while time.monotonic() < deadline:
        if path.is_file():
            return
        time.sleep(0.25)
    raise ValueError(f"{name} marker timed out")


def _injected_git_commit() -> str:
    value = os.environ.get("JOBFEED_MIGRATION_GIT_COMMIT", "")
    if len(value) != _GIT_COMMIT_LENGTH or any(
        character not in "0123456789abcdef" for character in value
    ):
        raise ValueError("migration git commit must be 40 lowercase hexadecimal")
    return value


def _alembic_executable() -> Path:
    value = shutil.which("alembic")
    if value is None:
        raise ValueError("alembic executable is unavailable in migration image")
    path = Path(value)
    if not path.is_absolute() or not path.is_file():
        raise ValueError("alembic executable must resolve to an absolute file")
    return path


def _publish_provenance(
    staging: Path,
    artifact_dir: Path,
    bundle: dict[str, object],
    pre_docs: object,
    post_docs: object,
) -> None:
    documents = {
        "restore-bootstrap.json": json.loads(RESTORE_BOOTSTRAP_PATH.read_text("utf-8")),
        "pre-inspection.json": pre_docs,
        "post-inspection.json": post_docs,
        "capture-ready.json": json.loads(RESTORE_CAPTURE_READY_PATH.read_text("utf-8")),
        "provenance-verified.json": json.loads(
            RESTORE_VERIFIED_PATH.read_text("utf-8")
        ),
        "formal-resource-fingerprints.json": json.loads(
            _FORMAL_RESOURCE_FINGERPRINT_PATH.read_text("utf-8")
        ),
    }
    provenance_index = build_provenance_index(documents, bundle["index"])
    validate_provenance_bundle(documents, bundle["index"], provenance_index)
    for filename, document in documents.items():
        _write_new_json(staging / filename, document)
    _write_new_json(staging / "provenance-index.json", provenance_index)
    _publish_directory_no_replace(staging, artifact_dir)


def _publish_directory_no_replace(staging: Path, destination: Path) -> None:
    """Claim and durably publish a bundle without replacing another run.

    Args:
        staging: Same-filesystem private directory containing complete files.
        destination: New public artifact directory claimed with mkdir exclusivity.

    Raises:
        FileExistsError: If another run already owns the destination.
        OSError: If moving or syncing any artifact fails.
    """
    os.mkdir(destination, mode=0o700)
    complete = False
    try:
        filenames = sorted(
            path.name
            for path in staging.iterdir()
            if path.name != "provenance-index.json"
        )
        filenames.append("provenance-index.json")
        for filename in filenames:
            (staging / filename).rename(destination / filename)
        _fsync_directory(destination)
        staging.rmdir()
        _fsync_directory(destination.parent)
        complete = True
    finally:
        if not complete:
            shutil.rmtree(destination, ignore_errors=True)


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


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
    staging: Path | None = None
    try:
        machine_token = os.environ.get(machine_token_env)
        if not machine_token:
            raise ValueError("benchmark machine token environment is empty")
        git_commit = _injected_git_commit()
        bootstrap = _wait_for_restore_bootstrap()
        config = PreprovisionedRestoreConfig(
            dump_path=Path("/run/jobfeed-migration/source.dump"),
            project_root=Path("/app"),
            alembic_executable=_alembic_executable(),
            source_dsn=SOURCE_RESTORE_DSN,
            scratch_dsn=SCRATCH_RESTORE_DSN,
            expected_project_label=bootstrap.project_label,
            bootstrap=bootstrap,
        )
        workload_document = json.loads(workload.read_text("utf-8"))
        bundle_holder: list[dict[str, object]] = []

        def capture(result: object) -> dict[str, object]:
            nonlocal staging
            restore = cast(Any, result)
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
            except Exception:
                shutil.rmtree(staging, ignore_errors=True)
                staging = None
                raise
            bundle: dict[str, object] = {
                "manifest": manifest,
                "benchmark": benchmark,
                "index": index,
            }
            bundle_holder.append(bundle)
            return bundle

        ready = capture_preprovisioned_restore(
            config,
            capture,
            evidence_bundle_sha256=artifact_sha256,
        )
        _wait_for_restore_file(RESTORE_POST_INSPECTION_PATH, "post-inspection")
        pre_docs = json.loads(_RESTORE_PRE_INSPECTION_PATH.read_text("utf-8"))
        post_docs = json.loads(RESTORE_POST_INSPECTION_PATH.read_text("utf-8"))
        bundle = bundle_holder[0]
        verify_preprovisioned_provenance(
            ProvenanceVerification(
                bootstrap=bootstrap,
                pre_docs=pre_docs,
                post_docs=post_docs,
                capture_ready=ready,
                actual_evidence_bundle_sha256=artifact_sha256(bundle),
            )
        )
        if staging is None:
            raise ValueError("baseline staging directory is unavailable")
        _publish_provenance(staging, artifact_dir, bundle, pre_docs, post_docs)
        staging = None
    except Exception as exc:
        if staging is not None:
            shutil.rmtree(staging, ignore_errors=True)
        if isinstance(exc, click.ClickException):
            raise
        raise click.ClickException(str(exc)) from exc


@migrate.command(name="_import-preprovisioned-snapshot", hidden=True)
@click.option("--artifact-dir", required=True, type=click.Path(path_type=Path))
def import_preprovisioned_snapshot_command(artifact_dir: Path) -> None:
    """Restore, import, verify, and atomically publish one cutover rehearsal.

    Args:
        artifact_dir: New output bundle directory inside the artifact mount.

    Raises:
        click.ClickException: Restore, import, parity, or provenance fails.
    """
    staging: Path | None = None
    try:
        git_commit = _injected_git_commit()
        bootstrap = _wait_for_restore_bootstrap()
        config = PreprovisionedRestoreConfig(
            dump_path=Path("/run/jobfeed-migration/source.dump"),
            project_root=Path("/app"),
            alembic_executable=_alembic_executable(),
            source_dsn=SOURCE_RESTORE_DSN,
            scratch_dsn=SCRATCH_RESTORE_DSN,
            expected_project_label=bootstrap.project_label,
            bootstrap=bootstrap,
        )
        bundle_holder: list[dict[str, object]] = []

        def capture(result: object) -> dict[str, object]:
            nonlocal staging
            restore = cast(Any, result)
            if artifact_dir.exists():
                raise FileExistsError(f"artifact bundle exists: {artifact_dir}")
            staging = Path(
                tempfile.mkdtemp(
                    prefix=f".{artifact_dir.name}.partial-", dir=artifact_dir.parent
                )
            )
            rehearsal = run_cutover_rehearsal(
                restore.source_dsn,
                destination=staging / "jobfeed.sqlite",
                source=CutoverSourceEvidence(
                    git_commit=git_commit,
                    dump_sha256=restore.dump_sha256,
                    dump_size_bytes=restore.dump_size_bytes,
                    restore_attestations=restore.attestations,
                ),
            )
            _write_new_json(staging / "snapshot-manifest.json", rehearsal.manifest)
            _write_new_json(staging / "import-result.json", rehearsal.import_result)
            _write_new_json(staging / "parity-result.json", rehearsal.parity_result)
            _write_new_json(staging / "cutover-evidence-index.json", rehearsal.index)
            bundle: dict[str, object] = {
                "manifest": rehearsal.manifest,
                "import_result": rehearsal.import_result,
                "parity_result": rehearsal.parity_result,
                "index": rehearsal.index,
            }
            bundle_holder.append(bundle)
            return bundle

        ready = capture_preprovisioned_restore(
            config,
            capture,
            evidence_bundle_sha256=artifact_sha256,
        )
        _wait_for_restore_file(RESTORE_POST_INSPECTION_PATH, "post-inspection")
        pre_docs = json.loads(_RESTORE_PRE_INSPECTION_PATH.read_text("utf-8"))
        post_docs = json.loads(RESTORE_POST_INSPECTION_PATH.read_text("utf-8"))
        bundle = bundle_holder[0]
        verify_preprovisioned_provenance(
            ProvenanceVerification(
                bootstrap=bootstrap,
                pre_docs=pre_docs,
                post_docs=post_docs,
                capture_ready=ready,
                actual_evidence_bundle_sha256=artifact_sha256(bundle),
            )
        )
        if staging is None:
            raise ValueError("cutover staging directory is unavailable")
        _publish_provenance(staging, artifact_dir, bundle, pre_docs, post_docs)
        staging = None
    except Exception as exc:
        if staging is not None:
            shutil.rmtree(staging, ignore_errors=True)
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
