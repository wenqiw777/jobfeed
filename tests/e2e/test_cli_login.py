"""E2E tests for one-time login CLI wiring."""

from __future__ import annotations

import sys
from pathlib import Path

from click.testing import CliRunner

from jobfeed.cli import cli
from jobfeed.config import SourcesLinkedInConfig

login_module = sys.modules["jobfeed.cli.login"]


def test_login_help_lists_linkedin() -> None:
    """``jobfeed login --help`` advertises the LinkedIn login target."""
    result = CliRunner().invoke(cli, ["login", "--help"])

    assert result.exit_code == 0
    assert "linkedin" in result.output


def test_login_linkedin_uses_configured_profile_dir(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """``jobfeed login linkedin`` uses the configured LinkedIn profile."""
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        "[sources.linkedin]\n"
        'profile_dir = "/tmp/jobfeed-login-profile"\n'
        'lock_path = "/tmp/jobfeed-login.lock"\n',
        encoding="utf-8",
    )
    captured: dict[str, str] = {}

    async def fake_open(config: SourcesLinkedInConfig) -> None:
        captured["profile_dir"] = config.profile_dir

    monkeypatch.setattr(login_module, "open_linkedin_login_browser", fake_open)

    result = CliRunner().invoke(
        cli,
        ["--config", str(config_path), "login", "linkedin"],
    )

    assert result.exit_code == 0, result.output
    assert captured == {"profile_dir": "/tmp/jobfeed-login-profile"}
    assert "LinkedIn login browser closed" in result.output
