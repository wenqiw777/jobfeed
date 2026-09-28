"""Bounded, exact request reuse for one evaluation run (never persistent)."""

from __future__ import annotations

import asyncio
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import dataclass, fields, is_dataclass, replace

from jobfeed.domain.models import LLMRequest, StageAResult, StageBResult

Result = StageAResult | StageBResult


@dataclass
class _Stored:
    stage: str
    client: object
    request: LLMRequest
    result: Result
    source_job_id: str
    size: int


@dataclass(eq=False)
class _Flight:
    stage: str
    client: object
    request: LLMRequest
    lock: asyncio.Lock
    users: int = 0


class ReuseEntry:
    """A request's lock-protected result; publish only after successful saving."""

    def __init__(self, cache: EvaluationReuse, flight: _Flight) -> None:
        self._cache = cache
        self._flight = flight
        stored = next((item for item in cache._stored if _matches(item, flight)), None)
        self.result = None if stored is None else deepcopy(stored.result)
        self.source_job_id = None if stored is None else stored.source_job_id

    def publish(self, result: Result, job_id: str) -> None:
        """Retain a successful result, with zero incremental cost on future hits.

        Args:
            result: Completed evaluation result.
            job_id: Job id supplied by the caller.
        """
        if self.result is not None:
            return
        flight = self._flight
        request = deepcopy(flight.request)
        saved = deepcopy(replace(result, cost_usd=0.0))
        size = _size(request) + _size(saved) + _size(job_id) + _size(flight.stage)
        cache = self._cache
        if size > cache._max_bytes or cache._max_entries < 1:
            return
        while cache._stored and (
            len(cache._stored) >= cache._max_entries
            or cache.retained_bytes + size > cache._max_bytes
        ):
            cache.retained_bytes -= cache._stored.pop(0).size
        cache._stored.append(
            _Stored(flight.stage, flight.client, request, saved, job_id, size)
        )
        cache.retained_bytes += size


class EvaluationReuse:
    """Linear exact equality avoids digests; active locks are never evicted."""

    def __init__(
        self, *, max_entries: int = 256, max_bytes: int = 16 * 1024 * 1024
    ) -> None:
        self._max_entries = max_entries
        self._max_bytes = max_bytes
        self._stored: list[_Stored] = []
        self._flights: list[_Flight] = []
        self.retained_bytes = 0

    @asynccontextmanager
    async def entry(
        self, stage: str, client: object, request: LLMRequest
    ) -> AsyncIterator[ReuseEntry]:
        """Serialize identical calls while leaving unrelated requests concurrent.

        Args:
            stage: Stage supplied by the caller.
            client: HTTP client for official source requests.
            request: Evaluation input used as the reuse key.

        Returns:
            Existing shared evaluation entry, or None.
        """
        probe = _Flight(stage, client, request, asyncio.Lock())
        flight = next((item for item in self._flights if _matches(item, probe)), None)
        if flight is None:
            flight = probe
            self._flights.append(flight)
        flight.users += 1
        try:
            async with flight.lock:
                yield ReuseEntry(self, flight)
        finally:
            flight.users -= 1
            if flight.users == 0:
                self._flights.remove(flight)


def _matches(item: _Stored | _Flight, other: _Flight) -> bool:
    return (
        item.stage == other.stage
        and item.client is other.client
        and item.request == other.request
    )


def _size(value: object) -> int:
    """Conservatively count repeated objects too; retain no growing size ledger."""
    total = sys.getsizeof(value)
    if is_dataclass(value) and not isinstance(value, type):
        total += sys.getsizeof(vars(value))
        total += sum(_size(getattr(value, field.name)) for field in fields(value))
    elif isinstance(value, dict):
        total += sum(_size(key) + _size(item) for key, item in value.items())
    elif isinstance(value, list | tuple):
        total += sum(_size(item) for item in value)
    return total
