"""Private, redacted output for failed evaluation CLI calls."""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path

from jobfeed.adapters.llm._subprocess import SubprocessError, SubprocessResult
from jobfeed.observability import JobfeedLogger


def redact_output(value: str) -> str:
    """Remove known environment secrets and credential-shaped log values.

    Args:
        value: Captured output to sanitize before persistence.

    Returns:
        Output with recognized secret values replaced by redaction markers.
    """
    for key, secret in os.environ.items():
        if secret and any(
            part in key.upper() for part in ("TOKEN", "SECRET", "PASSWORD", "API_KEY")
        ):
            value = value.replace(secret, "[REDACTED]")
    value = re.sub(r"(?i)(Bearer\s+)[A-Za-z0-9._~+/=-]+", r"\1[REDACTED]", value)
    value = re.sub(
        r'(?i)(["\']?(?:access_token|refresh_token|id_token|api_key|authorization)["\']?\s*[:=]\s*["\'])([^"\']+)',
        r"\1[REDACTED]",
        value,
    )
    return re.sub(r"\bsk-[A-Za-z0-9_-]+", "[REDACTED]", value)


def save_failure_output(
    *,
    model: str,
    attempt: int,
    error: Exception,
    result: SubprocessResult | None,
    logger: JobfeedLogger,
) -> Path | None:
    """Save both output streams without truncation; never mask the original error.

    Args:
        model: Model used for the failed call.
        attempt: One-based attempt number.
        error: Failure to describe, including output for subprocess errors.
        result: Captured output when the subprocess completed successfully.
        logger: Logger for the diagnostic path or persistence failure.

    Returns:
        Private diagnostic file path, or None if writing fails.
    """
    stdout = (
        error.stdout
        if isinstance(error, SubprocessError)
        else (result.stdout if result else "")
    )
    stderr = (
        error.stderr
        if isinstance(error, SubprocessError)
        else (result.stderr if result else "")
    )
    returncode = (
        error.returncode
        if isinstance(error, SubprocessError)
        else (result.returncode if result else None)
    )
    directory = Path.home() / ".local/share/jobfeed/diagnostics/codex"
    try:
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor, name = tempfile.mkstemp(
            prefix="failure-", suffix=".json", dir=directory
        )
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            json.dump(
                {
                    "model": model,
                    "attempt": attempt,
                    "returncode": returncode,
                    "error": redact_output(str(error)),
                    "stdout": redact_output(stdout),
                    "stderr": redact_output(stderr),
                },
                output,
                ensure_ascii=False,
            )
    except OSError:
        logger.warning("codex_failure_output_unavailable")
        return None
    path = Path(name)
    logger.warning(
        "codex_failure_output_saved",
        diagnostic_path=str(path),
        model=model,
        attempt=attempt,
    )
    return path
