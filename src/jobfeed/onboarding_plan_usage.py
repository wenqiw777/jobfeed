"""Read a signed-in Codex account's live plan-usage window."""

from __future__ import annotations

import asyncio
import json
import shutil
from dataclasses import dataclass
from typing import Any

_TIMEOUT_SECONDS = 8.0
_PERCENT_MAX = 100


class PlanUsageUnavailable(RuntimeError):
    """Raised when the local CLI cannot provide a usable allowance snapshot."""


@dataclass(frozen=True, kw_only=True)
class PlanUsageSnapshot:
    """Non-secret subset of the Codex account rate-limit response."""

    plan_name: str
    used_percent: int
    remaining_percent: int
    window_minutes: int | None
    resets_at: int | None


class CodexPlanUsageReader:
    """Query Codex app-server for the authenticated account's live allowance."""

    async def read(self) -> PlanUsageSnapshot:
        """Return the primary Codex plan window without exposing account secrets.

        Returns:
            Non-secret plan name, usage percentage, and reset metadata.

        Raises:
            PlanUsageUnavailable: If Codex cannot return a valid live window.
        """
        if shutil.which("codex") is None:
            raise PlanUsageUnavailable("Codex CLI is not installed")
        process = await asyncio.create_subprocess_exec(
            "codex",
            "app-server",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        if process.stdin is None or process.stdout is None:
            raise PlanUsageUnavailable("Codex app-server streams are unavailable")
        try:
            await _send(
                process,
                {
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "clientInfo": {"name": "jobfeed", "version": "0.1"},
                        "capabilities": {"experimentalApi": True},
                    },
                },
            )
            await _read_result(process, request_id=1)
            await _send(process, {"method": "initialized", "params": {}})
            await _send(
                process,
                {"id": 2, "method": "account/rateLimits/read", "params": None},
            )
            result = await _read_result(process, request_id=2)
            return _parse_snapshot(result)
        except (
            BrokenPipeError,
            json.JSONDecodeError,
            KeyError,
            TypeError,
            ValueError,
        ) as exc:
            raise PlanUsageUnavailable("Codex returned unreadable plan usage") from exc
        except TimeoutError as exc:
            raise PlanUsageUnavailable("Codex plan usage timed out") from exc
        finally:
            await _stop(process)


async def _send(process: asyncio.subprocess.Process, payload: dict[str, Any]) -> None:
    assert process.stdin is not None
    process.stdin.write(json.dumps(payload, separators=(",", ":")).encode() + b"\n")
    await process.stdin.drain()


async def _read_result(
    process: asyncio.subprocess.Process, *, request_id: int
) -> dict[str, Any]:
    assert process.stdout is not None
    async with asyncio.timeout(_TIMEOUT_SECONDS):
        while line := await process.stdout.readline():
            payload = json.loads(line)
            if payload.get("id") != request_id:
                continue
            result = payload.get("result")
            if not isinstance(result, dict):
                raise ValueError("missing result")
            return result
    raise ValueError("Codex app-server closed before replying")


def _parse_snapshot(result: dict[str, Any]) -> PlanUsageSnapshot:
    rate_limits = result.get("rateLimitsByLimitId")
    bucket = rate_limits.get("codex") if isinstance(rate_limits, dict) else None
    if not isinstance(bucket, dict):
        bucket = result.get("rateLimits")
    if not isinstance(bucket, dict):
        raise ValueError("missing Codex rate limits")
    primary = bucket.get("primary")
    if not isinstance(primary, dict):
        raise ValueError("missing primary rate-limit window")
    used_percent = primary.get("usedPercent")
    if not isinstance(used_percent, int) or not 0 <= used_percent <= _PERCENT_MAX:
        raise ValueError("invalid used percent")
    plan_type = bucket.get("planType")
    if not isinstance(plan_type, str) or not plan_type:
        raise ValueError("missing plan type")
    return PlanUsageSnapshot(
        plan_name=_plan_label(plan_type),
        used_percent=used_percent,
        remaining_percent=_PERCENT_MAX - used_percent,
        window_minutes=_optional_int(primary.get("windowDurationMins")),
        resets_at=_optional_int(primary.get("resetsAt")),
    )


def _optional_int(value: object) -> int | None:
    return value if isinstance(value, int) else None


def _plan_label(plan_type: str) -> str:
    labels = {
        "self_serve_business_prolite": "Business",
        "self_serve_business_usage_based": "Business",
        "enterprise_cbp_automation": "Enterprise",
        "enterprise_cbp_usage_based": "Enterprise",
        "prolite": "Pro",
        "ent26": "Enterprise",
    }
    return labels.get(plan_type, plan_type.replace("_", " ").title())


async def _stop(process: asyncio.subprocess.Process) -> None:
    if process.stdin is not None:
        process.stdin.close()
    if process.returncode is not None:
        return
    process.terminate()
    try:
        await asyncio.wait_for(process.wait(), timeout=1.0)
    except TimeoutError:
        process.kill()
        await process.wait()


__all__ = [
    "CodexPlanUsageReader",
    "PlanUsageSnapshot",
    "PlanUsageUnavailable",
]
