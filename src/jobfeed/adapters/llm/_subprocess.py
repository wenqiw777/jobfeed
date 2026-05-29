"""Shared async subprocess execution for CLI-based LLM adapters."""

from __future__ import annotations

import asyncio
import os
import signal
import time
from dataclasses import dataclass

from jobfeed.observability import JobfeedLogger


@dataclass(frozen=True, kw_only=True)
class SubprocessOptions:
    """Options for a single subprocess invocation."""

    input_text: str | None = None
    timeout_s: float | None = None
    start_new_session: bool = False
    cwd: str | None = None
    env: dict[str, str] | None = None


@dataclass(frozen=True, kw_only=True)
class SubprocessResult:
    """Captured output from a completed subprocess."""

    stdout: str
    stderr: str
    returncode: int
    elapsed_ms: int


class SubprocessTimeout(Exception):
    """Subprocess exceeded timeout and was killed."""


class SubprocessError(Exception):
    """Subprocess exited with non-zero code."""

    def __init__(self, returncode: int, stderr: str) -> None:
        self.returncode = returncode
        self.stderr = stderr
        super().__init__(f"exit code {returncode}: {stderr[:200]}")


@dataclass(frozen=True, kw_only=True)
class RetryOptions:
    """Retry policy for subprocess execution."""

    max_retries: int = 2
    retry_delay_s: float = 5.0


async def run_subprocess(
    cmd: list[str],
    *,
    options: SubprocessOptions,
    logger: JobfeedLogger,
) -> SubprocessResult:
    """Run a subprocess with optional timeout.

    Args:
        cmd: Command and arguments to execute.
        options: Input, timeout, and session options.
        logger: Structured logger for diagnostics.

    Returns:
        Captured stdout, stderr, return code, and elapsed time.

    Raises:
        SubprocessTimeout: When the process exceeds ``options.timeout_s``.
        SubprocessError: When the process exits with a non-zero code.
    """
    start = time.monotonic()
    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=options.start_new_session,
        cwd=options.cwd,
        env=options.env,
    )

    input_bytes = (
        options.input_text.encode() if options.input_text is not None else None
    )

    try:
        raw_stdout, raw_stderr = await asyncio.wait_for(
            process.communicate(input_bytes),
            timeout=options.timeout_s,
        )
    except TimeoutError:
        _kill_timed_out_process(process, options)
        await process.wait()
        raise SubprocessTimeout(
            f"{cmd[0]} timed out after {options.timeout_s}s",
        ) from None

    elapsed_ms = int((time.monotonic() - start) * 1000)
    stdout_text = raw_stdout.decode()
    stderr_text = raw_stderr.decode()

    logger.info(
        "subprocess_completed",
        command=cmd[0],
        elapsed_ms=elapsed_ms,
        returncode=process.returncode,
    )

    if process.returncode != 0:
        raise SubprocessError(process.returncode or 1, stderr_text)

    return SubprocessResult(
        stdout=stdout_text,
        stderr=stderr_text,
        returncode=process.returncode or 0,
        elapsed_ms=elapsed_ms,
    )


def _kill_timed_out_process(
    process: asyncio.subprocess.Process,
    options: SubprocessOptions,
) -> None:
    if options.start_new_session:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            return
        return
    process.kill()


_DEFAULT_RETRY = RetryOptions()


async def run_with_retry(
    cmd: list[str],
    *,
    options: SubprocessOptions,
    retry: RetryOptions = _DEFAULT_RETRY,
    logger: JobfeedLogger,
) -> SubprocessResult:
    """Run a subprocess with retry on infrastructure failure.

    Args:
        cmd: Command and arguments to execute.
        options: Input, timeout, and session options.
        retry: Retry policy (max attempts and delay).
        logger: Structured logger for diagnostics.

    Returns:
        Captured result from the first successful attempt.

    Raises:
        SubprocessTimeout: After all retries exhausted on timeout.
        SubprocessError: After all retries exhausted on non-zero exit.
    """
    for attempt in range(1 + retry.max_retries):
        try:
            return await run_subprocess(cmd, options=options, logger=logger)
        except (SubprocessTimeout, SubprocessError) as exc:
            if attempt >= retry.max_retries:
                raise
            logger.warning(
                "subprocess_retry",
                attempt=attempt + 1,
                error=str(exc),
            )
            await asyncio.sleep(retry.retry_delay_s)

    # Unreachable: the loop always returns or raises. This satisfies mypy.
    msg = "retry loop exited without returning"
    raise RuntimeError(msg)


__all__ = [
    "RetryOptions",
    "SubprocessError",
    "SubprocessOptions",
    "SubprocessResult",
    "SubprocessTimeout",
    "run_subprocess",
    "run_with_retry",
]
