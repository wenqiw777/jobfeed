"""Concurrent UI readers share work without retaining stale learning status."""
import asyncio

import pytest

from jobfeed.personal_ml_learning import (
    PersonalMLLearningService,
    PersonalMLObservation,
)


class Store:
    def __init__(self):
        self.calls = 0
        self.ready = asyncio.Event()

    async def list_personal_ml_observations(self, *, quick_pass_threshold):
        assert quick_pass_threshold > 0
        self.calls += 1
        await self.ready.wait()
        return [PersonalMLObservation(quick_pass=True)]


async def test_parallel_readers_share_one_read_but_next_read_is_fresh():
    store = Store()
    service = PersonalMLLearningService(store)
    tasks = [asyncio.create_task(service.status(quick_pass_threshold=70, enabled=True))
             for _ in range(3)]
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    store.ready.set()
    results = await asyncio.gather(*tasks)
    assert store.calls == 1
    assert results[0] == results[1] == results[2]
    await service.status(quick_pass_threshold=70, enabled=True)
    assert store.calls == 2  # noqa: PLR2004


async def test_cancelling_one_reader_does_not_cancel_another():
    store = Store()
    service = PersonalMLLearningService(store)
    first = asyncio.create_task(service.status(quick_pass_threshold=70, enabled=True))
    second = asyncio.create_task(service.status(quick_pass_threshold=70, enabled=True))
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    store.ready.set()
    assert (await second).label_count == 1
    assert store.calls == 1


async def test_failed_shared_read_does_not_poison_next_request():
    class FailingStore(Store):
        async def list_personal_ml_observations(self, *, quick_pass_threshold):
            result = await super().list_personal_ml_observations(
                quick_pass_threshold=quick_pass_threshold
            )
            if self.calls == 1:
                raise RuntimeError("temporary read failure")
            return result

    store = FailingStore()
    store.ready.set()
    service = PersonalMLLearningService(store)
    with pytest.raises(RuntimeError, match="temporary read failure"):
        await service.status(quick_pass_threshold=70, enabled=True)
    status = await service.status(quick_pass_threshold=70, enabled=True)
    assert status.label_count == 1
