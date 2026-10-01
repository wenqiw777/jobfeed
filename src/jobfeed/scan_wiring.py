"""Construct scan services with optional durable Redis journaling."""

from typing import cast

from jobfeed.adapters.llm._pricing import load_price_table
from jobfeed.adapters.queue.scan_journal import RedisScanJournal
from jobfeed.adapters.sources.official_search import CodexWebSearch, OfficialSearch
from jobfeed.application_resolution_wiring import build_application_resolver
from jobfeed.config import IntermediarySettings
from jobfeed.observability import JobfeedLogger
from jobfeed.ports.intermediary import IntermediaryStore
from jobfeed.ports.store import JobStore
from jobfeed.services.intermediary_resolution import IntermediaryResolver
from jobfeed.services.jobright_bridge import JobrightBridge
from jobfeed.services.run_orchestration import RunLeaseOrchestrator
from jobfeed.services.scan import ScanService


def build_scan_service(  # noqa: PLR0913 - existing runtime wiring plus resolution policy
    store: JobStore,
    logger: JobfeedLogger,
    run_orchestrator: RunLeaseOrchestrator | None = None,
    *,
    intermediary: IntermediarySettings | None = None,
    redis_url: str | None = None,
    redis_namespace: str = "jobfeed",
    bridge: JobrightBridge | None = None,
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
    resolver = None
    if (
        intermediary is not None
        and intermediary.enabled
        and hasattr(store, "official_candidates")
    ):
        capable = cast(IntermediaryStore, store)
        search = None
        if intermediary.external_search:
            llm = CodexWebSearch(
                model=intermediary.search_model,
                timeout_s=intermediary.search_timeout_s
                - min(5, intermediary.search_timeout_s / 2),
                max_retries=0,
                price_table=load_price_table(),
                logger=logger,
            )
            search = OfficialSearch(llm, capable, model=intermediary.search_model)
        resolver = IntermediaryResolver(
            capable,
            search=search,
            max_searches=intermediary.max_searches_per_scan,
            timeout_s=intermediary.search_timeout_s,
        )
    return ScanService(
        store,
        logger,
        run_orchestrator,
        journal=journal,
        intermediary=resolver,
        application_resolver=build_application_resolver(bridge)
        if callable(getattr(store, "record_application_identity", None))
        else None,
    )
