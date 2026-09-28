"""Construct scan services with optional durable Redis journaling."""

from jobfeed.adapters.queue.scan_journal import RedisScanJournal
from jobfeed.observability import JobfeedLogger
from jobfeed.ports.store import JobStore
from jobfeed.services.run_orchestration import RunLeaseOrchestrator
from jobfeed.services.scan import ScanService


def build_scan_service(
    store: JobStore,
    logger: JobfeedLogger,
    run_orchestrator: RunLeaseOrchestrator | None = None,
    *,
    redis_url: str | None = None,
    redis_namespace: str = "jobfeed",
) -> ScanService:
    """Wire source orchestration to a configured journal adapter.

    Args:
        store: Job and run persistence.
        logger: Structured scan logger.
        run_orchestrator: Existing lease owner, if configured.
        redis_url: Redis endpoint; None disables journaling.
        redis_namespace: Prefix isolating this application's queue keys.

    Returns:
        A service with queue I/O confined to its injected adapter.
    """
    journal = (
        RedisScanJournal(store, logger, url=redis_url, namespace=redis_namespace)
        if redis_url is not None
        else None
    )
    return ScanService(store, logger, run_orchestrator, journal=journal)
