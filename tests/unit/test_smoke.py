"""Smoke tests for the Phase 0 project scaffold."""

from __future__ import annotations

import tomllib
from pathlib import Path

from jobfeed.cli import cli


def test_cli_imports() -> None:
    """The installed CLI module should expose the Click command group."""
    assert cli.name == "jobfeed"


def test_example_config_is_parseable() -> None:
    """The example config should remain valid TOML for local development."""
    config_path = Path(__file__).resolve().parents[2] / "config.example.toml"

    parsed = tomllib.loads(config_path.read_text(encoding="utf-8"))

    assert parsed["db"]["url"].startswith("postgresql://")
