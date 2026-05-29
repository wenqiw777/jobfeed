"""Unit tests for shared subprocess helpers used by CLI-based LLM adapters."""

from __future__ import annotations

import asyncio
import signal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jobfeed.adapters.llm._subprocess import (
    RetryOptions,
    SubprocessError,
    SubprocessOptions,
    SubprocessResult,
    SubprocessTimeout,
    run_subprocess,
    run_with_retry,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

CMD = ["llm-cli", "--model", "test"]
EXPECTED_RETRY_CALLS = 2  # 1 initial + 1 retry


def _make_logger() -> MagicMock:
    """Return a mock satisfying the JobfeedLogger protocol."""
    logger = MagicMock()
    logger.info = MagicMock()
    logger.warning = MagicMock()
    logger.error = MagicMock()
    logger.debug = MagicMock()
    return logger


def _make_process(
    *,
    stdout: bytes = b"ok",
    stderr: bytes = b"",
    returncode: int = 0,
) -> MagicMock:
    """Build a mock asyncio.Process with async communicate/wait."""
    proc = MagicMock()
    proc.communicate = AsyncMock(return_value=(stdout, stderr))
    proc.wait = AsyncMock()
    proc.kill = MagicMock()
    proc.returncode = returncode
    proc.pid = 12345
    return proc


# ---------------------------------------------------------------------------
# run_subprocess — success
# ---------------------------------------------------------------------------


async def test_run_subprocess_returns_result_on_success() -> None:
    """run_subprocess returns stdout, stderr, returncode on success."""
    proc = _make_process(stdout=b"hello world", stderr=b"dbg", returncode=0)
    logger = _make_logger()

    with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)):
        result = await run_subprocess(
            CMD,
            options=SubprocessOptions(),
            logger=logger,
        )

    assert isinstance(result, SubprocessResult)
    assert result.stdout == "hello world"
    assert result.stderr == "dbg"
    assert result.returncode == 0


# ---------------------------------------------------------------------------
# run_subprocess — timeout
# ---------------------------------------------------------------------------


async def test_run_subprocess_raises_timeout() -> None:
    """run_subprocess raises SubprocessTimeout when timeout expires."""
    proc = _make_process()
    proc.communicate = AsyncMock(side_effect=asyncio.TimeoutError)
    logger = _make_logger()

    with (
        patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)),
        pytest.raises(SubprocessTimeout),
    ):
        await run_subprocess(
            CMD,
            options=SubprocessOptions(timeout_s=1.0),
            logger=logger,
        )

    proc.kill.assert_called_once()
    proc.wait.assert_awaited_once()


async def test_run_subprocess_timeout_kills_process_group() -> None:
    """start_new_session timeout kills the subprocess process group."""
    proc = _make_process()
    proc.communicate = AsyncMock(side_effect=asyncio.TimeoutError)
    logger = _make_logger()

    with (
        patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)),
        patch("jobfeed.adapters.llm._subprocess.os.killpg") as killpg,
        pytest.raises(SubprocessTimeout),
    ):
        await run_subprocess(
            CMD,
            options=SubprocessOptions(timeout_s=1.0, start_new_session=True),
            logger=logger,
        )

    killpg.assert_called_once_with(proc.pid, signal.SIGKILL)
    proc.kill.assert_not_called()
    proc.wait.assert_awaited_once()


# ---------------------------------------------------------------------------
# run_subprocess — non-zero exit
# ---------------------------------------------------------------------------


async def test_run_subprocess_raises_error_on_nonzero_exit() -> None:
    """run_subprocess raises SubprocessError on non-zero exit code."""
    proc = _make_process(stderr=b"bad input", returncode=1)
    logger = _make_logger()

    with (
        patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)),
        pytest.raises(SubprocessError) as exc_info,
    ):
        await run_subprocess(
            CMD,
            options=SubprocessOptions(),
            logger=logger,
        )

    assert exc_info.value.returncode == 1
    assert "bad input" in exc_info.value.stderr


# ---------------------------------------------------------------------------
# run_with_retry — retries on timeout then succeeds
# ---------------------------------------------------------------------------


async def test_run_with_retry_succeeds_on_second_attempt() -> None:
    """run_with_retry retries on SubprocessTimeout, succeeds next."""
    fail_proc = _make_process()
    fail_proc.communicate = AsyncMock(side_effect=asyncio.TimeoutError)

    ok_proc = _make_process(stdout=b"success", returncode=0)
    logger = _make_logger()

    mock_exec = AsyncMock(side_effect=[fail_proc, ok_proc])

    with (
        patch("asyncio.create_subprocess_exec", mock_exec),
        patch("asyncio.sleep", new_callable=AsyncMock),
    ):
        result = await run_with_retry(
            CMD,
            options=SubprocessOptions(timeout_s=1.0),
            retry=RetryOptions(max_retries=2, retry_delay_s=0.0),
            logger=logger,
        )

    assert result.stdout == "success"
    logger.warning.assert_called_once()


# ---------------------------------------------------------------------------
# run_with_retry — gives up after max_retries
# ---------------------------------------------------------------------------


async def test_run_with_retry_gives_up_after_max_retries() -> None:
    """run_with_retry raises after exhausting retry budget."""
    fail_proc = _make_process()
    fail_proc.communicate = AsyncMock(side_effect=asyncio.TimeoutError)

    logger = _make_logger()
    mock_exec = AsyncMock(return_value=fail_proc)

    with (
        patch("asyncio.create_subprocess_exec", mock_exec),
        patch("asyncio.sleep", new_callable=AsyncMock),
        pytest.raises(SubprocessTimeout),
    ):
        await run_with_retry(
            CMD,
            options=SubprocessOptions(timeout_s=1.0),
            retry=RetryOptions(max_retries=1, retry_delay_s=0.0),
            logger=logger,
        )

    assert mock_exec.call_count == EXPECTED_RETRY_CALLS


# ---------------------------------------------------------------------------
# start_new_session passed through
# ---------------------------------------------------------------------------


async def test_start_new_session_passed_through() -> None:
    """start_new_session=True is forwarded to create_subprocess_exec."""
    proc = _make_process()
    logger = _make_logger()
    mock_exec = AsyncMock(return_value=proc)

    with patch("asyncio.create_subprocess_exec", mock_exec):
        await run_subprocess(
            CMD,
            options=SubprocessOptions(start_new_session=True),
            logger=logger,
        )

    _, kwargs = mock_exec.call_args
    assert kwargs["start_new_session"] is True


async def test_cwd_and_env_passed_through() -> None:
    """cwd and env options are forwarded to create_subprocess_exec."""
    proc = _make_process()
    logger = _make_logger()
    mock_exec = AsyncMock(return_value=proc)
    env = {"PATH": "/bin"}

    with patch("asyncio.create_subprocess_exec", mock_exec):
        await run_subprocess(
            CMD,
            options=SubprocessOptions(cwd="/tmp/jobfeed-empty", env=env),
            logger=logger,
        )

    _, kwargs = mock_exec.call_args
    assert kwargs["cwd"] == "/tmp/jobfeed-empty"
    assert kwargs["env"] == env


# ---------------------------------------------------------------------------
# elapsed time measured
# ---------------------------------------------------------------------------


async def test_elapsed_time_is_positive() -> None:
    """Elapsed time in result is greater than zero."""
    proc = _make_process()
    logger = _make_logger()

    with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)):
        result = await run_subprocess(
            CMD,
            options=SubprocessOptions(),
            logger=logger,
        )

    assert result.elapsed_ms >= 0


# ---------------------------------------------------------------------------
# input text passed via stdin
# ---------------------------------------------------------------------------


async def test_input_text_passed_via_stdin() -> None:
    """Input text is encoded and sent to process stdin."""
    proc = _make_process(stdout=b"processed")
    logger = _make_logger()

    with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)):
        result = await run_subprocess(
            CMD,
            options=SubprocessOptions(input_text="hello prompt"),
            logger=logger,
        )

    proc.communicate.assert_awaited_once()
    input_bytes = proc.communicate.call_args[0][0]
    assert input_bytes == b"hello prompt"
    assert result.stdout == "processed"
