"""E2E tests for the Phase 0 Click CLI walking skeleton (PostgreSQL backend)."""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner, Result

from jobfeed.adapters.store.postgres import PostgresStore
from jobfeed.cli import cli, create_app

MOCK_JOB_COUNT = 3
CLICK_USAGE_ERROR = 2
FIXTURE_LEGACY_DB = (
    Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "legacy_v16.db"
)


def test_cli_full_chain_uses_configured_database(
    tmp_path: Path, fresh_pg_dsn: str
) -> None:
    """scan -> evaluate -> digest should work against the configured Postgres DB.

    Args:
        tmp_path: Temporary root used for the config file.
        fresh_pg_dsn: DSN of a freshly migrated, empty Postgres database.
    """
    config_path = write_config(tmp_path, "full-chain", fresh_pg_dsn)
    runner = CliRunner()

    scan = invoke_ok(runner, config_path, "scan", "--source", "mock")
    evaluate = invoke_ok(runner, config_path, "evaluate")
    digest = invoke_ok(runner, config_path, "digest")

    assert "Discovered 3 jobs, inserted 3, updated 0" in scan.output
    assert f"Evaluated {MOCK_JOB_COUNT} (Stage A), {MOCK_JOB_COUNT} (Stage B)" in (
        evaluate.output
    )
    assert "# Daily Digest" in digest.output
    assert "Backend Platform Intern" in digest.output


def test_cli_dry_run_does_not_persist_evaluations(
    tmp_path: Path, fresh_pg_dsn: str
) -> None:
    """evaluate --dry-run should not call the LLM or create digest rows.

    Args:
        tmp_path: Temporary root used for the config file.
        fresh_pg_dsn: DSN of a freshly migrated, empty Postgres database.
    """
    config_path = write_config(tmp_path, "dry-run", fresh_pg_dsn)
    runner = CliRunner()

    invoke_ok(runner, config_path, "scan", "--source", "mock")
    dry_run = invoke_ok(runner, config_path, "evaluate", "--dry-run")
    digest = invoke_ok(runner, config_path, "digest")

    assert "Evaluated 0 (Stage A), 0 (Stage B)" in dry_run.output
    assert "Backend Platform Intern" not in digest.output


def test_cli_digest_accepts_timezone_aware_cutoff(
    tmp_path: Path, fresh_pg_dsn: str
) -> None:
    """digest --cutoff-at should expose the domain cutoff split."""
    config_path = write_config(tmp_path, "digest-cutoff", fresh_pg_dsn)
    runner = CliRunner()

    invoke_ok(runner, config_path, "scan", "--source", "mock")
    invoke_ok(runner, config_path, "evaluate")
    digest = invoke_ok(
        runner,
        config_path,
        "digest",
        "--cutoff-at",
        "2026-05-21T00:00:00+00:00",
    )

    assert "# Daily Digest" in digest.output
    assert "Backend Platform Intern" in digest.output


def test_cli_verbose_enables_debug_output(tmp_path: Path, fresh_pg_dsn: str) -> None:
    """--verbose should add debug log output without changing command behavior.

    Args:
        tmp_path: Temporary root used for the config files.
        fresh_pg_dsn: DSN of a freshly migrated, empty Postgres database.
    """
    quiet_config = write_config(tmp_path, "quiet", fresh_pg_dsn)
    verbose_config = write_config(tmp_path, "verbose", fresh_pg_dsn)
    runner = CliRunner()

    quiet = invoke_ok(runner, quiet_config, "scan", "--source", "mock")
    verbose = invoke_ok(
        runner,
        verbose_config,
        "--verbose",
        "scan",
        "--source",
        "mock",
    )

    assert "cli_verbose_enabled" not in quiet.output
    assert "cli_verbose_enabled" in verbose.output
    assert len(verbose.output.splitlines()) > len(quiet.output.splitlines())


def test_cli_rejects_missing_explicit_config(tmp_path: Path) -> None:
    """A typo in --config should fail without a Python traceback.

    Args:
        tmp_path: Temporary root used for the missing config path.
    """
    result = CliRunner().invoke(
        cli,
        ["--config", str(tmp_path / "missing.toml"), "scan"],
    )

    assert result.exit_code == 1
    assert "Config file not found" in result.output
    assert "Traceback" not in result.output


def test_create_app_wires_postgres_store(tmp_path: Path) -> None:
    """create_app should build a PostgresStore (the only supported backend).

    Construction does not open a connection, so this stays offline.

    Args:
        tmp_path: Temporary root used for a synthetic config file.
    """
    config_path = tmp_path / "config.toml"
    config_path.write_text("[db]\n", encoding="utf-8")

    app = create_app(config_path)

    assert isinstance(app["store"], PostgresStore)


def test_cli_migrate_dry_run_needs_no_target_store(tmp_path: Path) -> None:
    """migrate import-sqlite --dry-run prints a plan without touching the store.

    The dry-run path never reaches the store (which would require a live
    database), so this stays offline.

    Args:
        tmp_path: Temporary root used for a synthetic config file.
    """
    config_path = tmp_path / "config.toml"
    config_path.write_text("[db]\n", encoding="utf-8")

    result = CliRunner().invoke(
        cli,
        [
            "--config",
            str(config_path),
            "migrate",
            "import-sqlite",
            "--from",
            str(FIXTURE_LEGACY_DB),
            "--dry-run",
        ],
    )

    assert result.exit_code == 0
    assert "Traceback" not in result.output


def test_cli_digest_rejects_timezone_naive_cutoff(tmp_path: Path) -> None:
    """digest --cutoff-at should reject timestamps without an offset.

    Cutoff validation fails during Click parsing, before the store is opened,
    so this stays offline.
    """
    config_path = tmp_path / "config.toml"
    config_path.write_text("[db]\n", encoding="utf-8")

    result = CliRunner().invoke(
        cli,
        [
            "--config",
            str(config_path),
            "digest",
            "--cutoff-at",
            "2026-05-21T00:00:00",
        ],
    )

    assert result.exit_code == CLICK_USAGE_ERROR
    assert "must include a timezone offset" in result.output


def invoke_ok(
    runner: CliRunner,
    config_path: Path,
    *args: str,
) -> Result:
    """Invoke the CLI and assert a zero exit code.

    Args:
        runner: Click test runner.
        config_path: Config file to pass through the top-level option.
        args: Command and command-specific arguments.

    Returns:
        Successful Click invocation result.

    Raises:
        AssertionError: If the command exits non-zero.
    """
    result = runner.invoke(cli, ["--config", str(config_path), *args])
    assert result.exit_code == 0, result.output
    return result


def write_config(tmp_path: Path, name: str, dsn: str) -> Path:
    """Write an isolated CLI config pointing at the test Postgres database.

    Args:
        tmp_path: Temporary root owned by pytest.
        name: Unique config namespace for one test flow.
        dsn: PostgreSQL DSN the store should connect to.

    Returns:
        Path to the written TOML config file.
    """
    config_dir = tmp_path / name
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
