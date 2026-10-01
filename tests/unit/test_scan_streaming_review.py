"""Independent checks of streamed batch identity and journal replay."""

import asyncio
import json
from dataclasses import replace

import pytest
import structlog

from jobfeed.adapters.sources.jobboard_extension import JobboardExtensionSource
from jobfeed.adapters.store.sqlite import SQLiteStore
from jobfeed.config_sources import SourcesBoardExtensionConfig
from jobfeed.domain.errors import RunLeaseLostError
from jobfeed.services.jobright_bridge import JobrightBridge
from jobfeed.services.pipeline_context import current_pipeline
from jobfeed.services.scan import ScanService
from jobfeed.services.scan_streaming import ScanBatchWriter
from tests.support.sqlite_jobs_evaluations import make_job


class ReviewPipeline:
    """Retain JSON journal outputs and manifests across a simulated restart."""

    root = "stream-review"

    def __init__(self):
        self.writer_lock = asyncio.Lock()
        self.results = {}
        self.partials = {}

    async def step(self, name, payload, work, *, retry_errors=False):
        json.dumps(payload)
        if name not in self.results:
            result = await work(payload)
            if retry_errors:
                result = {**result, "generation": "1"}
            self.results[name] = json.loads(json.dumps(result))
        return self.results[name]

    async def load_partial(self, name):
        return self.partials.get(name, [])

    async def save_partial(self, name, rows):
        self.partials.setdefault(name, []).extend(json.loads(json.dumps(rows)))


class ReviewStore(SQLiteStore):
    """Track actual database receipt writes independently of replayed results."""

    def __init__(self, path):
        super().__init__(path)
        self.receipts = []
        self.saved = asyncio.Event()

    async def save_job_batch(self, jobs, **kwargs):
        self.receipts.append(kwargs["receipt_key"])
        saved = await super().save_job_batch(jobs, **kwargs)
        self.saved.set()
        return saved


async def test_duplicate_identity_within_one_batch_is_saved_once():
    saved = []

    async def save(batch, *_generation):
        saved.extend(batch)

    posting = make_job("same")
    async with ScanBatchWriter(save) as writer:
        await writer.submit([posting, replace(posting)])
    assert len(saved) == 1


async def test_completed_source_replay_reuses_original_stream_write_receipts(tmp_path):
    class Source:
        fetches = 0

        async def fetch_jobs(self, _config):
            raise AssertionError("Streaming expected")

        async def fetch_jobs_streaming(self, _config, _progress, on_batch):
            self.fetches += 1
            jobs = [make_job("streamed")]
            await on_batch(jobs)
            return jobs

    store = ReviewStore(tmp_path / "stream-replay.db")
    await store.connect()
    pipeline, source = ReviewPipeline(), Source()
    token = current_pipeline.set(pipeline)
    try:
        first = await ScanService(store, structlog.get_logger("review")).run(
            [("mock", source, {})]
        )
        second = await ScanService(store, structlog.get_logger("review")).run(
            [("mock", source, {})]
        )
        assert source.fetches == 1
        assert len(store.receipts) == 1
        assert first.jobs_inserted == second.jobs_inserted == 1
        assert second.jobs_updated == 0
    finally:
        current_pipeline.reset(token)
        await store.close()


async def test_abrupt_source_crash_replays_original_postings_before_refetch(tmp_path):
    store = ReviewStore(tmp_path / "stream-crash-replay.db")
    await store.connect()

    class Source:
        fetches = 0
        original = make_job("streamed")

        async def fetch_jobs(self, _config):
            raise AssertionError("Streaming expected")

        async def fetch_jobs_streaming(self, _config, _progress, on_batch):
            self.fetches += 1
            posting = self.original
            if self.fetches > 1:
                posting = replace(posting, enriched_at=None, jd_text="Remapped body")
            await on_batch([posting])
            if self.fetches == 1:
                await store.saved.wait()
                raise asyncio.CancelledError()
            return [posting]

    pipeline, source = ReviewPipeline(), Source()
    token = current_pipeline.set(pipeline)
    try:
        with pytest.raises(asyncio.CancelledError):
            await ScanService(store, structlog.get_logger("review")).run(
                [("mock", source, {})]
            )
        second = await ScanService(store, structlog.get_logger("review")).run(
            [("mock", source, {})]
        )
        expected_fetches = 2
        assert source.fetches == expected_fetches
        assert len(store.receipts) == 1
        assert second.jobs_inserted == 1 and second.jobs_updated == 0
        loaded = (await store.list_jobs())[0]
        assert loaded.jd_text == source.original.jd_text
        assert loaded.enriched_at == source.original.enriched_at
    finally:
        current_pipeline.reset(token)
        await store.close()


async def test_generic_source_error_preserves_already_accepted_pending_batch():
    saved = []
    release = asyncio.Event()

    async def save(batch, _generation):
        await release.wait()
        saved.extend(batch)

    with pytest.raises(ValueError, match="source failed after accepting batch"):
        async with ScanBatchWriter(save) as writer:
            await writer.submit([make_job("accepted")])
            release.set()
            raise ValueError("source failed after accepting batch")
    assert len(saved) == 1


@pytest.mark.parametrize("error", [asyncio.CancelledError(), RunLeaseLostError("lost")])
async def test_interruption_cancels_pending_batch_without_new_writes(error):
    saved = []
    release = asyncio.Event()

    async def save(batch, _generation):
        await release.wait()
        saved.extend(batch)

    with pytest.raises(type(error)):
        async with ScanBatchWriter(save) as writer:
            await writer.submit([make_job("interrupted")])
            release.set()
            raise error
    assert saved == []


async def test_failed_database_writer_propagates_its_original_exception():
    async def save(_batch, _generation):
        raise RuntimeError("database refused accepted batch")

    with pytest.raises(RuntimeError, match="database refused accepted batch"):
        async with ScanBatchWriter(save) as writer:
            await writer.submit([make_job("db-failure")])


async def test_extension_adapter_remapped_dates_do_not_change_replay_receipt(tmp_path):
    store = ReviewStore(tmp_path / "extension-stream-crash.db")
    await store.connect()
    raw = {
        "source": "linkedin",
        "id": "1",
        "title": "Backend Engineer",
        "url": "https://www.linkedin.com/jobs/view/1/",
        "employer": {"name": "Example Corp"},
        "locations": "New York, NY",
        "description": "Original platform description",
        "applyUrl": "https://careers.example.test/jobs/1",
    }

    class Bridge:
        supported_sources = frozenset({"linkedin"})
        calls = 0

        async def run_scan(self, **kwargs):
            self.calls += 1
            await kwargs["on_batch"]([raw])
            if self.calls == 1:
                await store.saved.wait()
                raise asyncio.CancelledError()
            return [raw]

    bridge = Bridge()
    source = JobboardExtensionSource(
        source="linkedin",
        config=SourcesBoardExtensionConfig(queries=["Engineer"], max_jobs=1),
        bridge=bridge,
    )
    token = current_pipeline.set(ReviewPipeline())
    try:
        with pytest.raises(asyncio.CancelledError):
            await ScanService(store, structlog.get_logger("review")).run(
                [("linkedin", source, {})]
            )
        original = (await store.list_jobs())[0]
        second = await ScanService(store, structlog.get_logger("review")).run(
            [("linkedin", source, {})]
        )
        assert second.jobs_inserted == 1 and second.jobs_updated == 0
        assert len(store.receipts) == 1
        loaded = (await store.list_jobs())[0]
        assert loaded.enriched_at == original.enriched_at
        assert loaded.jd_text == raw["description"]
    finally:
        current_pipeline.reset(token)
        await store.close()


async def test_durable_bridge_serializes_payload_without_live_batch_callback():
    bridge = JobrightBridge()
    connection = bridge.connect(["linkedin"])
    batches = []

    async def on_batch(rows):
        batches.extend(rows)

    token = current_pipeline.set(ReviewPipeline())
    task = asyncio.create_task(
        bridge.run_scan(
            source="linkedin",
            max_jobs=1,
            batch_size=1,
            pacing_s=0,
            timeout_s=5,
            on_progress=lambda _: None,
            on_batch=on_batch,
        )
    )
    current_pipeline.reset(token)
    try:
        command = await connection.next_command()
        row = {"source": "linkedin", "id": "1"}
        await bridge.receive(
            {"type": "batch", "task_id": command["task_id"], "jobs": [row]}
        )
        await bridge.receive({"type": "complete", "task_id": command["task_id"]})
        assert await task == [row]
        assert batches == [row]
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
