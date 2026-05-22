"""E2E tests for the Phase 0 Click CLI walking skeleton."""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner, Result

from jobfeed.cli import cli

MOCK_JOB_COUNT = 3
CLICK_USAGE_ERROR = 2


def test_cli_full_chain_uses_isolated_temp_database(tmp_path: Path) -> None:
    """scan -> evaluate -> digest should work against a temp SQLite DB.

    Args:
        tmp_path: Temporary root used for both config and SQLite state.
    """
    config_path = write_config(tmp_path, "full-chain")
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
    assert expected_db_path(tmp_path, "full-chain").exists()


def test_cli_dry_run_does_not_persist_evaluations(tmp_path: Path) -> None:
    """evaluate --dry-run should not call the LLM or create digest rows.

    Args:
        tmp_path: Temporary root used for config and SQLite state.
    """
    config_path = write_config(tmp_path, "dry-run")
    runner = CliRunner()

    invoke_ok(runner, config_path, "scan", "--source", "mock")
    dry_run = invoke_ok(runner, config_path, "evaluate", "--dry-run")
    digest = invoke_ok(runner, config_path, "digest")

    assert "Evaluated 0 (Stage A), 0 (Stage B)" in dry_run.output
    assert "Backend Platform Intern" not in digest.output


def test_cli_digest_accepts_timezone_aware_cutoff(tmp_path: Path) -> None:
    """digest --cutoff-at should expose the domain cutoff split."""
    config_path = write_config(tmp_path, "digest-cutoff")
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


def test_cli_verbose_enables_debug_output(tmp_path: Path) -> None:
    """--verbose should add debug log output without changing command behavior.

    Args:
        tmp_path: Temporary root used for config and SQLite state.
    """
    quiet_config = write_config(tmp_path, "quiet")
    verbose_config = write_config(tmp_path, "verbose")
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


def test_cli_rejects_unsupported_db_backend(tmp_path: Path) -> None:
    """Unsupported db.backend values should fail before command execution.

    Args:
        tmp_path: Temporary root used for a synthetic config file.
    """
    config_path = tmp_path / "config.toml"
    config_path.write_text('[db]\nbackend = "postgres"\n', encoding="utf-8")

    result = CliRunner().invoke(cli, ["--config", str(config_path), "scan"])

    assert result.exit_code == 1
    assert "Phase 0 supports only sqlite db backend" in result.output
    assert "Traceback" not in result.output


def test_cli_digest_rejects_timezone_naive_cutoff(tmp_path: Path) -> None:
    """digest --cutoff-at should reject timestamps without an offset."""
    result = CliRunner().invoke(
        cli,
        [
            "--config",
            str(write_config(tmp_path, "bad-cutoff")),
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


def write_config(tmp_path: Path, name: str) -> Path:
    """Write an isolated Phase 0 config for CLI tests.

    Args:
        tmp_path: Temporary root owned by pytest.
        name: Unique config/database namespace for one test flow.

    Returns:
        Path to the written TOML config file.
    """
    config_dir = tmp_path / name
    config_dir.mkdir(parents=True, exist_ok=True)
    config_path = config_dir / "config.toml"
    db_path = expected_db_path(tmp_path, name)
    config_path.write_text(
        "\n".join(
            [
                "[db]",
                'backend = "sqlite"',
                f'sqlite_path = "{db_path}"',
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


def expected_db_path(tmp_path: Path, name: str) -> Path:
    """Return the temp DB path used by one E2E config.

    Args:
        tmp_path: Temporary root owned by pytest.
        name: Unique config/database namespace for one test flow.

    Returns:
        SQLite database path under pytest's temp directory.
    """
    return tmp_path / name / ".jobfeed-dev" / "test.db"
