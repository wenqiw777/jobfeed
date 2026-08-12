"""Shared typed command and configuration values for restore rehearsal."""

from __future__ import annotations

import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class CommandResult:
    """Captured result of one argv-only subprocess invocation."""

    returncode: int
    stdout: str
    stderr: str


class CommandRunner(Protocol):
    """Execute an argv sequence without a shell and capture its result."""

    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
    ) -> CommandResult:
        """Run one command and return its exit code and output.

        Args:
            argv: Exact command argument vector; no shell syntax is accepted.
            cwd: Optional explicit working directory.
            env: Optional complete child environment.

        Returns:
            Captured command result without implicit success checking.
        """


@dataclass(frozen=True, kw_only=True)
class RestoreTarget:
    """Explicit isolated container, host port, and optional named volume."""

    container_name: str
    host_port: int
    volume_name: str | None = None


@dataclass(frozen=True, kw_only=True)
class RestoreRehearsalConfig:
    """Inputs allowed to select a two-restore rehearsal environment."""

    dump_path: Path
    project_root: Path
    output_dir: Path
    alembic_executable: Path
    source: RestoreTarget
    scratch: RestoreTarget
    postgres_image: str = "postgres:16"
    database_name: str = "jobfeed_restore"
    database_user: str = "jobfeed_restore"


@dataclass(frozen=True, kw_only=True)
class RestoreRehearsalResult:
    """Derived evidence and live DSNs supplied only to the capture callback."""

    attestations: dict[str, dict[str, object]]
    source_dsn: str
    scratch_dsn: str
    staged_dump_path: Path
    dump_sha256: str
    dump_size_bytes: int


class SubprocessRunner:
    """Run bounded subprocess argv directly without invoking a shell."""

    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
    ) -> CommandResult:
        """Run one command with captured output and an upper time bound.

        Args:
            argv: Exact command argument vector; no shell is invoked.
            cwd: Optional explicit working directory.
            env: Optional complete child environment.

        Returns:
            Captured exit code, standard output, and standard error.

        Raises:
            OSError: If the executable cannot be started.
            subprocess.TimeoutExpired: If the command exceeds 30 minutes.
        """
        completed = subprocess.run(
            list(argv),
            cwd=cwd,
            env=dict(env) if env is not None else None,
            check=False,
            capture_output=True,
            text=True,
            timeout=1800,
        )
        return CommandResult(completed.returncode, completed.stdout, completed.stderr)


def checked(result: CommandResult, label: str) -> CommandResult:
    """Return successful command output or raise with bounded diagnostics.

    Args:
        result: Captured subprocess result.
        label: Stable operation name for the error message.

    Returns:
        The unchanged successful result.

    Raises:
        RuntimeError: If the command exit code is nonzero.
    """
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "no command output"
        raise RuntimeError(f"{label} failed: {detail}")
    return result
