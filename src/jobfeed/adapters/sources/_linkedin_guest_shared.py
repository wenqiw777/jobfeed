"""One process-local Guest queue/cache shared by discovery, enrichment and dedupe."""

import asyncio
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from time import monotonic
from urllib.parse import urlsplit
from weakref import WeakKeyDictionary

import httpx

from jobfeed.adapters.sources._linkedin_guest_http import GuestResponse, fetch

_CACHE_LIMIT = 512
_CACHE_SECONDS = 3600
_FAILURE_SECONDS = 300


class _GuestRequests:
    def __init__(self) -> None:
        self.lock = asyncio.Lock()
        self.cache: OrderedDict[str, tuple[float, GuestResponse]] = OrderedDict()
        self.last_start = 0.0
        self.blocked_until = 0.0

    async def read(
        self,
        client: httpx.AsyncClient,
        url: str,
        sleep: Callable[[float], Awaitable[None]],
    ) -> GuestResponse:
        # Serialize the network work, so cancellation leaves no detached request
        # and concurrent readers of a posting reuse the first completed response.
        async with self.lock:
            cached = self.cache.get(url)
            if cached and cached[0] > monotonic():
                return cached[1]
            if self.blocked_until > monotonic():
                return GuestResponse(429, "")
            remaining = 1.0 - (monotonic() - self.last_start)
            if remaining > 0:
                await sleep(remaining)
            self.last_start = monotonic()
            response = await fetch(client, url, sleep=sleep)
            if response.status in {401, 403, 429, 999}:
                self.blocked_until = monotonic() + _FAILURE_SECONDS
            lifetime = (
                _CACHE_SECONDS
                if response.status == httpx.codes.OK
                else _FAILURE_SECONDS
            )
            # Search pages must stay fresh; only detail bodies are reusable.
            if "/jobs/api/jobPosting/" in urlsplit(url).path:
                self.cache[url] = (monotonic() + lifetime, response)
                self.cache.move_to_end(url)
                while len(self.cache) > _CACHE_LIMIT:
                    self.cache.popitem(last=False)
            return response


_queues: WeakKeyDictionary[asyncio.AbstractEventLoop, _GuestRequests] = (
    WeakKeyDictionary()
)


async def shared_fetch(
    client: httpx.AsyncClient,
    url: str,
    *,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> GuestResponse:
    """Read anonymously with shared pacing, response reuse and block cooldown."""
    loop = asyncio.get_running_loop()
    queue = _queues.setdefault(loop, _GuestRequests())
    return await queue.read(client, url, sleep)
