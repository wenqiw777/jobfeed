"""Foreground admissions wait for historical cleanup and durable lease release."""

import asyncio

import pytest

from jobfeed.domain.errors import RunConflictError
from tests.unit.test_run_manager import RecordingStore, _build_manager


async def trigger(manager, kind):
    return (
        await manager.trigger_scan([])
        if kind == "scan"
        else await manager.trigger_evaluate(scope="backlog")
    )


class BlockingService:
    def __init__(self):
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def run(self, *_args, **_kwargs):
        self.entered.set()
        await self.release.wait()


@pytest.mark.parametrize("kind", ["scan", "evaluate"])
async def test_foreground_preempts_backfill_only_after_full_cleanup(kind):
    manager = _build_manager()
    entered, cleaning, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def work(_session, _progress):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaning.set()
            await release.wait()

    old_id = await manager.trigger_application_backfill(work)
    await entered.wait()
    foreground = asyncio.create_task(trigger(manager, kind))
    try:
        await asyncio.wait_for(cleaning.wait(), 1)
        assert not foreground.done()
        assert manager._store.lease_calls == ["start"]
        release.set()
        new_id = await asyncio.wait_for(foreground, 1)
        new_task = manager._tasks.get(new_id)
        if new_task:
            await new_task
        old = await manager._store.get_pipeline_run(old_id)
        assert old.failure_code == "foreground_priority"
        assert old.status == "failed"
        assert manager._store.lease_calls[:3] == ["start", "finalize", "start"]
    finally:
        release.set()
        foreground.cancel()
        await asyncio.gather(foreground, return_exceptions=True)
        await manager.shutdown()


@pytest.mark.parametrize("kind", ["scan", "evaluate"])
async def test_ordinary_same_kind_busy_and_backfill_foreground_busy_are_rejected(kind):
    service = BlockingService()
    manager = _build_manager(scan_service=service, eval_service=service)
    await trigger(manager, kind)
    await service.entered.wait()
    try:
        with pytest.raises(RunConflictError):
            await trigger(manager, kind)
        with pytest.raises(RunConflictError):
            await manager.trigger_application_backfill(lambda *_: None)
    finally:
        service.release.set()
        await manager.shutdown()


async def test_concurrent_foreground_scans_cancel_cleanup_only_once():
    service = BlockingService()
    manager = _build_manager(scan_service=service)
    entered, cleaning, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def work(_session, _progress):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaning.set()
            await release.wait()

    old = await manager.trigger_application_backfill(work)
    await entered.wait()
    old_task = manager._tasks[old]
    first = asyncio.create_task(manager.trigger_scan([]))
    second = asyncio.create_task(manager.trigger_scan([]))
    try:
        await asyncio.wait_for(cleaning.wait(), 1)
        assert old_task.cancelling() == 1
        assert not first.done() and not second.done()
        release.set()
        results = await asyncio.wait_for(
            asyncio.gather(first, second, return_exceptions=True), 1
        )
        assert sum(isinstance(value, str) for value in results) == 1
        assert sum(isinstance(value, RunConflictError) for value in results) == 1
        assert old_task.cancelling() == 1
    finally:
        release.set()
        service.release.set()
        for task in (first, second):
            task.cancel()
        await asyncio.gather(first, second, return_exceptions=True)
        await manager.shutdown()


async def test_foreground_waits_for_backfill_setup_before_preempting_registration():
    starting, release_setup, entered = asyncio.Event(), asyncio.Event(), asyncio.Event()

    class SlowStartStore(RecordingStore):
        async def start_run_with_lease(self, run, **kwargs):
            if run.source == "application-backfill":
                starting.set()
                await release_setup.wait()
            return await super().start_run_with_lease(run, **kwargs)

    manager = _build_manager(store=SlowStartStore())

    async def work(_session, _progress):
        entered.set()
        await asyncio.Event().wait()

    backfill = asyncio.create_task(manager.trigger_application_backfill(work))
    await starting.wait()
    foreground = asyncio.create_task(manager.trigger_evaluate(scope="backlog"))
    try:
        await asyncio.sleep(0)
        assert not foreground.done()
        release_setup.set()
        old = await asyncio.wait_for(backfill, 1)
        new = await asyncio.wait_for(foreground, 1)
        task = manager._tasks.get(new)
        if task:
            await task
        assert (
            await manager._store.get_pipeline_run(old)
        ).failure_code == "foreground_priority"
        assert entered.is_set()
    finally:
        release_setup.set()
        for task in (backfill, foreground):
            task.cancel()
        await asyncio.gather(backfill, foreground, return_exceptions=True)
        await manager.shutdown()


async def test_new_backfill_cannot_register_between_preemption_and_evaluate_admission():
    service = BlockingService()
    manager = _build_manager(eval_service=service)
    entered, cleaning, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def work(_session, _progress):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaning.set()
            await release.wait()

    await manager.trigger_application_backfill(work)
    await entered.wait()
    foreground = asyncio.create_task(manager.trigger_evaluate(scope="backlog"))
    second = None
    try:
        await asyncio.wait_for(cleaning.wait(), 1)
        second = asyncio.create_task(manager.trigger_application_backfill(work))
        release.set()
        await asyncio.wait_for(foreground, 1)
        with pytest.raises(RunConflictError):
            await asyncio.wait_for(second, 1)
    finally:
        release.set()
        service.release.set()
        tasks = [foreground] + ([second] if second else [])
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await manager.shutdown()


async def test_cancelled_foreground_caller_does_not_recancel_backfill_finalization():
    manager = _build_manager()
    entered, cleaning, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def work(_session, _progress):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaning.set()
            await release.wait()

    old = await manager.trigger_application_backfill(work)
    await entered.wait()
    old_task = manager._tasks[old]
    foreground = asyncio.create_task(manager.trigger_scan([]))
    try:
        await asyncio.wait_for(cleaning.wait(), 1)
        foreground.cancel()
        with pytest.raises(asyncio.CancelledError):
            await foreground
        assert old_task.cancelling() == 1
        assert not old_task.done()
        release.set()
        await asyncio.gather(old_task, return_exceptions=True)
        assert (
            await manager._store.get_pipeline_run(old)
        ).failure_code == "foreground_priority"
        assert manager._store.lease_calls == ["start", "finalize"]
    finally:
        release.set()
        foreground.cancel()
        await asyncio.gather(foreground, return_exceptions=True)
        await manager.shutdown()


@pytest.mark.parametrize("reason", ["user_stopped", "interrupted"])
async def test_explicit_stop_or_shutdown_overrides_priority_without_recancelling(
    reason,
):
    manager = _build_manager()
    entered, cleaning, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def work(_session, _progress):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaning.set()
            await release.wait()

    old = await manager.trigger_application_backfill(work)
    await entered.wait()
    task = manager._tasks[old]
    foreground = asyncio.create_task(manager.trigger_scan([]))
    explicit = None
    try:
        await asyncio.wait_for(cleaning.wait(), 1)
        explicit = asyncio.create_task(
            manager.stop_run(old) if reason == "user_stopped" else manager.shutdown()
        )
        await asyncio.sleep(0)
        assert task.cancelling() == 1
        assert not task.done()
        release.set()
        await asyncio.wait_for(explicit, 1)
        result = (await asyncio.gather(foreground, return_exceptions=True))[0]
        if reason == "interrupted":
            assert isinstance(result, RunConflictError)
            assert manager._store.lease_calls == ["start", "finalize"]
        else:
            assert isinstance(result, str)
        assert (await manager._store.get_pipeline_run(old)).failure_code == reason
    finally:
        release.set()
        tasks = [foreground] + ([explicit] if explicit else [])
        for pending in tasks:
            pending.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await manager.shutdown()


async def test_foreground_waits_for_durable_finalizer_not_only_work_cleanup():
    entered, finalizing, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

    class SlowFinalizeStore(RecordingStore):
        async def finalize_run_with_lease(self, run, **kwargs):
            if run.source == "application-backfill":
                finalizing.set()
                await release.wait()
            return await super().finalize_run_with_lease(run, **kwargs)

    manager = _build_manager(store=SlowFinalizeStore())

    async def work(_session, _progress):
        entered.set()
        await asyncio.Event().wait()

    await manager.trigger_application_backfill(work)
    await entered.wait()
    foreground = asyncio.create_task(manager.trigger_evaluate(scope="backlog"))
    try:
        await asyncio.wait_for(finalizing.wait(), 1)
        assert not foreground.done()
        assert manager._store.lease_calls == ["start"]
        release.set()
        await asyncio.wait_for(foreground, 1)
        assert manager._store.lease_calls[:3] == ["start", "finalize", "start"]
    finally:
        release.set()
        foreground.cancel()
        await asyncio.gather(foreground, return_exceptions=True)
        await manager.shutdown()
