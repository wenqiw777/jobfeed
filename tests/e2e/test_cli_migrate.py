"""E2E tests for the migrate CLI commands."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from jobfeed.cli import cli

FIXTURE_DIR = Path(__file__).resolve().parent.parent / "fixtures"
LEGACY_DB = FIXTURE_DIR / "legacy_v16.db"
MANIFEST_JSON = FIXTURE_DIR / "legacy_v16_manifest.json"
GENERATOR = FIXTURE_DIR / "generate_legacy_fixture.py"


@pytest.fixture(autouse=True, scope="module")
def ensure_fixture_exists() -> None:
    """Generate the legacy fixture DB if it doesn't exist."""
    if not LEGACY_DB.exists() or not MANIFEST_JSON.exists():
        subprocess.run(
            [sys.executable, str(GENERATOR)],
            check=True,
            capture_output=True,
        )
    assert LEGACY_DB.exists(), f"Fixture DB not found: {LEGACY_DB}"
    assert MANIFEST_JSON.exists(), f"Manifest not found: {MANIFEST_JSON}"


def test_inspect_sqlite_exits_zero_and_prints_counts() -> None:
    """inspect-sqlite on the fixture DB exits 0 and prints row counts."""
    runner = CliRunner()
    result = runner.invoke(cli, ["migrate", "inspect-sqlite", str(LEGACY_DB)])

    assert result.exit_code == 0, f"Exit code {result.exit_code}: {result.output}"
    assert "Schema version: 16" in result.output
    assert "Row counts:" in result.output
    assert "jobs" in result.output
    assert "evaluations" in result.output
    assert "Health: OK" in result.output


def test_inspect_sqlite_shows_table_counts() -> None:
    """inspect-sqlite prints the correct row count for each table."""
    runner = CliRunner()
    result = runner.invoke(cli, ["migrate", "inspect-sqlite", str(LEGACY_DB)])

    assert result.exit_code == 0, result.output
    # The fixture has 20 jobs
    assert "20" in result.output
    # And 15 evaluations
    assert "15" in result.output


def test_import_sqlite_with_verify_exits_zero(
    tmp_path: Path, fresh_pg_dsn: str
) -> None:
    """import-sqlite --from fixture --verify exits 0 and parity passes.

    Args:
        tmp_path: Temporary directory for the config file.
        fresh_pg_dsn: DSN of a freshly migrated, empty Postgres database.
    """
    runner = CliRunner()
    config_path = _write_import_config(tmp_path, fresh_pg_dsn)

    result = runner.invoke(
        cli,
        [
            "--config",
            str(config_path),
            "migrate",
            "import-sqlite",
            "--from",
            str(LEGACY_DB),
            "--verify",
            "--manifest",
            str(MANIFEST_JSON),
        ],
    )

    assert result.exit_code == 0, f"Exit code {result.exit_code}: {result.output}"
    assert "Import completed successfully" in result.output
    assert "All parity checks passed" in result.output


def test_import_sqlite_verifies_by_default_with_derived_manifest(
    tmp_path: Path, fresh_pg_dsn: str
) -> None:
    """Default import (no --verify, no --manifest) still runs parity.

    Verification is on by default and derives expected counts from the source
    DB, so no checked-in fixture manifest is needed.

    Args:
        tmp_path: Temporary directory for the config file.
        fresh_pg_dsn: DSN of a freshly migrated, empty Postgres database.
    """
    runner = CliRunner()
    config_path = _write_import_config(tmp_path, fresh_pg_dsn)

    result = runner.invoke(
        cli,
        [
            "--config",
            str(config_path),
            "migrate",
            "import-sqlite",
            "--from",
            str(LEGACY_DB),
        ],
    )

    assert result.exit_code == 0, f"Exit code {result.exit_code}: {result.output}"
    assert "All parity checks passed" in result.output


def test_import_sqlite_no_verify_skips_parity(
    tmp_path: Path, fresh_pg_dsn: str
) -> None:
    """--no-verify imports without running parity checks.

    Args:
        tmp_path: Temporary directory for the config file.
        fresh_pg_dsn: DSN of a freshly migrated, empty Postgres database.
    """
    runner = CliRunner()
    config_path = _write_import_config(tmp_path, fresh_pg_dsn)

    result = runner.invoke(
        cli,
        [
            "--config",
            str(config_path),
            "migrate",
            "import-sqlite",
            "--from",
            str(LEGACY_DB),
            "--no-verify",
        ],
    )

    assert result.exit_code == 0, f"Exit code {result.exit_code}: {result.output}"
    assert "Import completed successfully" in result.output
    assert "parity" not in result.output.lower()


def test_import_sqlite_nonexistent_db_exits_one() -> None:
    """import-sqlite --from nonexistent.db exits 1 with a clear error."""
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["migrate", "import-sqlite", "--from", "/tmp/nonexistent_db_12345.db"],
    )

    assert result.exit_code == 1, f"Exit code {result.exit_code}: {result.output}"
    assert "not found" in result.output


def test_import_sqlite_dry_run_exits_zero_no_data_written(tmp_path: Path) -> None:
    """import-sqlite --dry-run prints plan without opening the target store.

    The dry-run path never connects, so this uses an unreachable DSN to prove
    no database access happens.

    Args:
        tmp_path: Temporary directory for the config file.
    """
    runner = CliRunner()
    config_path = _write_import_config(tmp_path, "postgresql://unused/none")

    result = runner.invoke(
        cli,
        [
            "--config",
            str(config_path),
            "migrate",
            "import-sqlite",
            "--from",
            str(LEGACY_DB),
            "--dry-run",
        ],
    )

    assert result.exit_code == 0, f"Exit code {result.exit_code}: {result.output}"
    assert "Dry run" in result.output
    assert "Tables to import:" in result.output
    assert "jobs" in result.output


def test_inspect_sqlite_nonexistent_path_exits_nonzero() -> None:
    """inspect-sqlite on a nonexistent path exits non-zero."""
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["migrate", "inspect-sqlite", "/tmp/nonexistent_db_12345.db"],
    )

    # Click's exists=True on the argument will cause exit code 2 (usage error)
    assert result.exit_code != 0


def _write_import_config(tmp_path: Path, dsn: str) -> Path:
    """Write a config file pointing the import target at a Postgres database.

    Args:
        tmp_path: Temporary directory for the test.
        dsn: PostgreSQL DSN the import target store should connect to.

    Returns:
        Path to the written TOML config file.
    """
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_path = config_dir / "config.toml"
    config_path.write_text(
        "\n".join(
            [
                "[db]",
                f'url = "{dsn}"',
                "",
                "[observability]",
                'log_level = "warning"',
                'log_format = "human"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    return config_path
