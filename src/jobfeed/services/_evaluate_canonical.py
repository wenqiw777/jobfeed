"""Run paid scoring once per real job while retaining source posting provenance."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Protocol

from jobfeed.config import Settings
from jobfeed.domain.errors import ScoringParseError
from jobfeed.domain.filtering import apply_hard_filters
from jobfeed.domain.models import (
    DryRunPreviewItem,
    LLMRequest,
    MLGateResult,
    PipelineRun,
)
from jobfeed.domain.real_job_evaluation import RealJobEvaluationInput
from jobfeed.domain.scoring_parse import parse_stage_a_response, parse_stage_b_response
from jobfeed.personal_ml_learning import PersonalMLLearningService
from jobfeed.ports.ml_gate import GateInput
from jobfeed.ports.store import JobStore
from jobfeed.services._evaluate_claims import STAGE_B_LEASE_HEARTBEAT_SECONDS
from jobfeed.services._evaluate_gate import gate_mode_for_state, resolve_gate_mode
from jobfeed.services._evaluate_helpers import UsageRecordContext, record_usage
from jobfeed.services._evaluate_seniority import apply_seniority_gate
from jobfeed.services.canonical_priority import CanonicalPriorityInput
from jobfeed.services.evaluate_types import (
    EvaluateRuntimeConfig,
    evaluation_runtime_config,
)
from jobfeed.services.run_orchestration import RunLeaseSession

if TYPE_CHECKING:
    from jobfeed.services.evaluate import EvaluateService


_ML_GATE_PROGRESS_BATCH_SIZE = 256


class _StageBClaimRefresher(Protocol):
    async def refresh_real_job_stage_a_claim(
        self, real_job_id: str, *, expected_revision: int, expected_generation: int
    ) -> bool: ...

    async def refresh_real_job_stage_b_claim(
        self, real_job_id: str, *, expected_revision: int, expected_generation: int
    ) -> bool: ...

    async def release_real_job_stage_a_claim(
        self, real_job_id: str, *, expected_revision: int, expected_generation: int
    ) -> bool: ...

    async def release_real_job_stage_b_claim(
        self, real_job_id: str, *, expected_revision: int, expected_generation: int
    ) -> bool: ...


@dataclass(frozen=True)
class _PolicySnapshot:
    stage_a_json: str
    stage_b_json: str
    gate_mode: str
    config: EvaluateRuntimeConfig

    def stage_a(self) -> dict[str, object]:
        return json.loads(self.stage_a_json)

    def stage_b(self) -> dict[str, object]:
        return json.loads(self.stage_b_json)


async def _policy_for_run(
    service: EvaluateService, *, dry_run: bool
) -> _PolicySnapshot:
    config = service._config
    del dry_run  # Preview compares against the paid policy, not skipped gate work.
    if service._deps.personal_ml is not None:
        status = await service._deps.personal_ml.status(
            quick_pass_threshold=config.stage_a_threshold,
            enabled=config.ml_gate_enabled,
        )
        mode = gate_mode_for_state(config.ml_gate_enabled, status.state)
    else:
        mode = await resolve_gate_mode(service._deps, config, dry_run=False)
    return _snapshot_for_config(config, mode)


async def current_policy_for_settings(
    settings: Settings, personal_ml: PersonalMLLearningService
) -> _PolicySnapshot:
    """Resolve the paid policy currently configured for API score reads.

    Args:
        settings: Effective GUI or loaded file configuration.
        personal_ml: Read-only learning lifecycle service.

    Returns:
        The same explicit policy fields used by a Stage A/B paid run.
    """
    config = evaluation_runtime_config(settings)
    status = await personal_ml.status(
        quick_pass_threshold=config.stage_a_threshold,
        enabled=config.ml_gate_enabled,
    )
    mode = gate_mode_for_state(config.ml_gate_enabled, status.state)
    return _snapshot_for_config(config, mode)


def _snapshot_for_config(
    config: EvaluateRuntimeConfig, mode: str
) -> _PolicySnapshot:
    """Freeze the explicit model and gate facts for one reader or runner."""
    stage_a = {
        "model": config.llm.stage_a,
        "prompt_version": config.stage_a_prompt_version,
        "resume_version": config.resume_version,
        "ml_gate_mode": mode,
        "ml_gate_model_version": (
            config.ml_gate_model_version if mode != "off" else None
        ),
        "ml_gate_threshold_override": (
            config.ml_gate_threshold_override if mode != "off" else None
        ),
        "ml_gate_policy_version": (
            config.ml_gate_policy_version if mode != "off" else None
        ),
        "seniority_gate_mode": config.seniority_gate_mode,
        "seniority_gate_model_version": (
            config.seniority_gate_model_version
            if config.seniority_gate_mode != "off" else None
        ),
        "seniority_gate_threshold": (
            config.seniority_gate_threshold
            if config.seniority_gate_mode != "off" else None
        ),
        "seniority_gate_policy_version": (
            config.seniority_gate_policy_version
            if config.seniority_gate_mode != "off" else None
        ),
    }
    stage_b = {
        "model": config.llm.stage_b,
        "prompt_version": config.stage_b_prompt_version,
        "resume_version": config.resume_version,
        "stage_a_threshold": config.stage_a_threshold,
    }
    return _PolicySnapshot(
        json.dumps(stage_a, sort_keys=True, separators=(",", ":")),
        json.dumps(stage_b, sort_keys=True, separators=(",", ":")),
        mode,
        config,
    )


async def _real_id_pages(
    service: EvaluateService,
    *,
    stage: str,
    explicit_real_ids: list[str] | None,
    policy: _PolicySnapshot,
) -> AsyncIterator[list[str]]:
    store = service._deps.store
    if explicit_real_ids is not None:
        if explicit_real_ids:
            yield explicit_real_ids
        return
    page_size = policy.config.ml_gate_max_candidates
    if page_size <= 0:
        return
    before_id: int | None = None
    while True:
        page = await store.list_real_job_ids_for_evaluation(
            limit=page_size,
            stage=stage,
            threshold=policy.config.stage_a_threshold,
            before_id=before_id,
            stage_a_policy=policy.stage_a(),
            stage_b_policy=policy.stage_b(),
        )
        if not page:
            return
        yield page
        if len(page) < page_size:
            return
        next_before = int(page[-1])
        if before_id is not None and next_before >= before_id:
            raise ValueError("canonical backlog cursor did not advance")
        before_id = next_before


async def build_canonical_dry_run_preview(  # noqa: PLR0913 - paged preview
    service: EvaluateService,
    run: PipelineRun,
    *,
    stage: str,
    limit: int,
    source_job_ids: list[str] | None,
    corpus: str,
    max_days: int | None,
) -> None:
    """Read one pending preview per parent without creating claim rows.

    Args:
        service: Evaluation service and read-only store.
        run: Run receiving preview rows and counters.
        stage: Requested scoring stage.
        limit: Maximum preview rows per stage.
        source_job_ids: Optional exact source scope.
        corpus: Failed-only or regular candidate scope.
        max_days: Optional posting age limit.
    """
    if limit <= 0:
        return
    store = service._deps.store
    run.evaluation_input_total = 0
    # Preview the policy a paid run would actually use; dry-run only controls
    # persistence, while personal-ML shadow mode can differ by that flag.
    policy = await _policy_for_run(service, dry_run=False)
    explicit_ids = (
        await store.resolve_real_job_ids(source_job_ids)
        if source_job_ids is not None else None
    )
    for target_stage in ("a", "b"):
        if stage in {target_stage, "both"}:
            await _preview_stage(
                service, run, target_stage=target_stage, limit=limit,
                explicit_ids=explicit_ids, corpus=corpus, max_days=max_days,
                policy=policy,
            )


async def _preview_stage(  # noqa: PLR0913 - explicit preview scope
    service: EvaluateService, run: PipelineRun, *, target_stage: str, limit: int,
    explicit_ids: list[str] | None, corpus: str, max_days: int | None,
    policy: _PolicySnapshot,
) -> None:
    """Page one requested stage and stop after enough preview rows."""
    async for page in _real_id_pages(
        service, stage=target_stage, explicit_real_ids=explicit_ids, policy=policy,
    ):
        run.evaluation_input_total += len(page)
        inputs = await service._deps.store.load_real_job_priority_inputs(page)
        _append_preview_page(
            service, run, inputs, target_stage=target_stage, limit=limit,
            corpus=corpus, max_days=max_days, policy=policy,
        )
        if sum(
            item.stage == f"stage_{target_stage}" for item in run.dry_run_preview
        ) >= limit:
            return


def _append_preview_page(  # noqa: PLR0913 - preview decision facts
    service: EvaluateService, run: PipelineRun,
    inputs: list[CanonicalPriorityInput], *, target_stage: str, limit: int,
    corpus: str, max_days: int | None, policy: _PolicySnapshot,
) -> None:
    """Append eligible rows from one bounded page."""
    previewed = sum(
        item.stage == f"stage_{target_stage}" for item in run.dry_run_preview
    )
    for item in inputs:
        if corpus == "failed" and item.stage_a_status != "error":
            continue
        if not _preview_eligible(item, max_days):
            continue
        if target_stage == "a" and (
            item.stage_a_status != "completed"
            or _preview_policy_stale(item, policy, stage="a")
        ):
            if (
                service._deps.hard_filters is not None
                and apply_hard_filters(item.job, service._deps.hard_filters)
                is not None
            ):
                run.jobs_filtered += 1
            else:
                run.dry_run_preview.append(_preview_item("stage_a", item))
                previewed += 1
        if (
            target_stage == "b"
            and item.stage_a_score is not None
            and item.stage_a_score >= policy.config.stage_a_threshold
            and not _preview_policy_stale(item, policy, stage="a")
            and (
                item.stage_b_status != "completed"
                or _preview_policy_stale(item, policy, stage="b")
            )
        ):
            run.dry_run_preview.append(_preview_item("stage_b", item))
            previewed += 1
        if previewed >= limit:
            return


def _preview_policy_stale(
    item: CanonicalPriorityInput, policy: _PolicySnapshot, *, stage: str
) -> bool:
    """Check the stored policy for a completed preview candidate."""
    facts = json.loads(item.input_facts_json or "{}")
    key = "stage_a_policy" if stage == "a" else "stage_b_policy"
    requested = policy.stage_a() if stage == "a" else policy.stage_b()
    return facts.get(key) != requested


def _preview_eligible(item: CanonicalPriorityInput, max_days: int | None) -> bool:
    if item.job.closed_at is not None or item.job.is_repost is True:
        return False
    if max_days is None:
        return True
    now = datetime.now(UTC)
    effective = item.job.posted_at
    if effective is None or effective > now:
        effective = item.job.discovered_at
    return effective >= now - timedelta(days=max_days)


def _preview_item(stage: str, item: CanonicalPriorityInput) -> DryRunPreviewItem:
    return DryRunPreviewItem(
        stage=stage,
        job_id=item.real_job_id,
        title=item.job.title,
        company=item.job.company,
    )


async def run_canonical_evaluation(  # noqa: PLR0913 - claim funnel
    service: EvaluateService,
    run: PipelineRun,
    session: RunLeaseSession,
    *,
    stage: str,
    limit: int,
    source_job_ids: list[str] | None,
    corpus: str,
    max_days: int | None,
) -> None:
    """Resolve source scope once, then claim and score canonical identities.

    Args:
        service: Evaluation service and paid dependencies.
        run: Run receiving stage counters and costs.
        session: Active run lease checked before every claim.
        stage: Requested scoring stage.
        limit: Maximum claims per stage.
        source_job_ids: Optional exact source scope.
        corpus: Failed-only or regular candidate scope.
        max_days: Optional posting age limit.

    Raises:
        ValueError: If bounded backlog pagination does not advance.
    """
    store = service._deps.store
    run.evaluation_input_total = 0
    policy = await _policy_for_run(service, dry_run=False)
    run.ml_gate_total = 0
    run.ml_gate_processed = 0
    run.jobs_gate_passed = 0
    explicit_ids = (
        await store.resolve_real_job_ids(source_job_ids)
        if source_job_ids is not None else None
    )
    if stage != "b" and limit > 0 and await service._budget.has_budget():
        run.stage_a_total = 0
        service._emit_progress(run)
        stage_a_claims = await _claim_stage_a_candidates(
            service, run, session, explicit_ids=explicit_ids, corpus=corpus,
            limit=limit, max_days=max_days, policy=policy,
        )
        run.stage_a_total = len(stage_a_claims)
        service._emit_progress(run)
        try:
            quick_claims = await _prepare_stage_a_claims(
                service, run, session, stage_a_claims, policy=policy,
            )
            run.stage_a_total = len(quick_claims)
            run.stage_a_processed = 0
            run.progress_stage = "stage_a"
            service._emit_progress(run)
            await _score_stage_a_claims(
                service, run, session, quick_claims, policy=policy,
            )
        except BaseException:
            await _release_stage_a_claims(
                store,
                stage_a_claims,
                max_concurrent=policy.config.llm.max_concurrent,
            )
            raise
    if stage != "a" and limit > 0 and await service._budget.has_budget():
        run.progress_stage = "stage_b"
        run.stage_b_total = 0
        service._emit_progress(run)
        stage_b_claims = await _claim_stage_b_candidates(
            service, run, session, explicit_ids=explicit_ids,
            count_inputs=stage == "b", limit=limit, max_days=max_days,
            policy=policy,
        )
        run.stage_b_total = len(stage_b_claims)
        service._emit_progress(run)
        await _score_stage_b_claims(
            service, run, session, stage_b_claims, policy=policy,
        )


async def _claim_stage_a_candidates(  # noqa: PLR0913 - bounded claim context
    service: EvaluateService, run: PipelineRun, session: RunLeaseSession,
    *, explicit_ids: list[str] | None, corpus: str, limit: int,
    max_days: int | None, policy: _PolicySnapshot,
) -> list[RealJobEvaluationInput]:
    """Discover and claim the complete bounded Stage A candidate set."""
    store = service._deps.store
    claimed: list[RealJobEvaluationInput] = []
    try:
        async for page in _real_id_pages(
            service, stage="a", explicit_real_ids=explicit_ids, policy=policy
        ):
            run.evaluation_input_total += len(page)
            if corpus == "failed":
                inputs = await store.load_real_job_priority_inputs(page)
                failed = {
                    item.real_job_id for item in inputs
                    if item.stage_a_status == "error"
                }
                candidates = [real_id for real_id in page if real_id in failed]
            else:
                candidates = page
            session.ensure_active()
            claimed.extend(await store.claim_real_job_stage_a_by_ids(
                candidates, limit=limit - len(claimed), max_days=max_days,
                stage_a_policy=policy.stage_a(), stage_b_policy=policy.stage_b(),
            ))
            run.stage_a_total = len(claimed)
            service._emit_progress(run)
            if len(claimed) >= limit:
                break
        return claimed
    except BaseException:
        await _release_stage_a_claims(
            store,
            claimed,
            max_concurrent=policy.config.llm.max_concurrent,
        )
        raise


async def _score_stage_a_claims(
    service: EvaluateService, run: PipelineRun, session: RunLeaseSession,
    claims: list[RealJobEvaluationInput], *, policy: _PolicySnapshot,
) -> None:
    """Score a discovered Stage A set with bounded concurrency."""
    store = service._deps.store
    semaphore = asyncio.Semaphore(max(1, policy.config.llm.max_concurrent))

    async def worker(item: RealJobEvaluationInput) -> None:
        try:
            async with semaphore:
                session.ensure_active()
                async with _maintain_real_job_claim(store, item, stage="a") as active:
                    if active:
                        await _score_a(service, run, session, item, policy=policy)
        except BaseException:
            await store.release_real_job_stage_a_claim(
                item.real_job_id, expected_revision=item.input_revision,
                expected_generation=item.claim_generation,
            )
            raise
        finally:
            run.stage_a_processed += 1
            service._emit_progress(run)

    workers = [asyncio.create_task(worker(item)) for item in claims]
    try:
        await asyncio.gather(*workers)
    except BaseException:
        for worker_task in workers:
            worker_task.cancel()
        await asyncio.gather(*workers, return_exceptions=True)
        raise


async def _prepare_stage_a_claims(
    service: EvaluateService, run: PipelineRun, session: RunLeaseSession,
    claims: list[RealJobEvaluationInput], *, policy: _PolicySnapshot,
) -> list[RealJobEvaluationInput]:
    """Move one complete candidate set through the pre-LLM stages."""
    store = service._deps.store
    hard_filter_survivors: list[RealJobEvaluationInput] = []
    hard_filter_rejected: list[RealJobEvaluationInput] = []
    for item in claims:
        reason = (
            apply_hard_filters(item.job, service._deps.hard_filters)
            if service._deps.hard_filters is not None else None
        )
        if reason is None:
            hard_filter_survivors.append(item)
        else:
            hard_filter_rejected.append(item)
    run.jobs_filtered += len(hard_filter_rejected)
    await _release_stage_a_claims(
        store,
        hard_filter_rejected,
        max_concurrent=policy.config.llm.max_concurrent,
    )

    run.progress_stage = "ml_gate"
    service._emit_progress(run)
    gate_survivors = await _gate_stage_a_claims(
        service, run, session, hard_filter_survivors, policy=policy,
    )

    run.progress_stage = "seniority_gate"
    service._emit_progress(run)
    if service._deps.seniority_gate is None:
        return gate_survivors
    jobs, blocked = await apply_seniority_gate(
        service._deps.seniority_gate,
        [item.job for item in gate_survivors],
        mode=policy.config.seniority_gate_mode,
    )
    run.jobs_seniority_filtered += blocked
    survivor_source_ids = {job.id for job in jobs}
    survivors = [
        item for item in gate_survivors if item.job.id in survivor_source_ids
    ]
    rejected = [
        item for item in gate_survivors if item.job.id not in survivor_source_ids
    ]
    await _release_stage_a_claims(
        store,
        rejected,
        max_concurrent=policy.config.llm.max_concurrent,
    )
    return survivors


async def _gate_stage_a_claims(
    service: EvaluateService, run: PipelineRun, session: RunLeaseSession,
    claims: list[RealJobEvaluationInput], *, policy: _PolicySnapshot,
) -> list[RealJobEvaluationInput]:
    """Run the SDE gate once for the complete post-rule candidate set."""
    store = service._deps.store
    run.ml_gate_total = len(claims)
    already_passed = [item for item in claims if item.ml_gate_result == "pass"]
    to_predict = [item for item in claims if item.ml_gate_result != "pass"]
    run.ml_gate_processed = len(already_passed)
    service._emit_progress(run)
    if policy.gate_mode == "off" or service._deps.ml_gate is None:
        run.ml_gate_processed = len(claims)
        run.jobs_gate_passed += len(claims)
        service._emit_progress(run)
        return claims
    semaphore = asyncio.Semaphore(max(1, policy.config.llm.max_concurrent))

    async def persist(
        item: RealJobEvaluationInput, result: MLGateResult,
    ) -> RealJobEvaluationInput | None:
        if (
            result.result == "fail"
            and result.fail_reason != "not software engineering role"
        ):
            result = replace(result, result="pass", fail_reason=None)
        async with semaphore:
            session.ensure_active()
            saved = await store.save_real_job_ml_gate(
                item.real_job_id,
                result,
                expected_revision=item.input_revision,
                expected_generation=item.claim_generation,
            )
        run.ml_gate_processed += 1
        service._emit_progress(run)
        if not saved:
            return None
        if (
            policy.gate_mode == "filter"
            and result.result == "fail"
            and int(item.real_job_id) % 10 != 0
        ):
            run.jobs_ml_gated += 1
            await store.release_real_job_stage_a_claim(
                item.real_job_id,
                expected_revision=item.input_revision,
                expected_generation=item.claim_generation,
            )
            return None
        return item

    predicted: list[RealJobEvaluationInput | None] = []
    for offset in range(0, len(to_predict), _ML_GATE_PROGRESS_BATCH_SIZE):
        session.ensure_active()
        batch = to_predict[offset:offset + _ML_GATE_PROGRESS_BATCH_SIZE]
        results = await service._deps.ml_gate.predict_batch([
            GateInput(
                job_id=item.real_job_id,
                title=item.job.title,
                jd_text=item.job.jd_text or "",
            )
            for item in batch
        ])
        if len(results) != len(batch):
            raise ValueError("ML gate returned the wrong number of decisions")
        predicted.extend(await asyncio.gather(*(
            persist(item, result)
            for item, result in zip(batch, results, strict=True)
        )))
    survivors = already_passed + [item for item in predicted if item is not None]
    run.jobs_gate_passed += len(survivors)
    return survivors


async def _release_stage_a_claims(
    store: JobStore,
    claims: list[RealJobEvaluationInput],
    *,
    max_concurrent: int,
) -> None:
    """Release Stage A claims without opening one connection per claim."""
    semaphore = asyncio.Semaphore(max(1, max_concurrent))

    async def release(item: RealJobEvaluationInput) -> None:
        async with semaphore:
            await store.release_real_job_stage_a_claim(  # type: ignore[attr-defined]
                item.real_job_id,
                expected_revision=item.input_revision,
                expected_generation=item.claim_generation,
            )

    await asyncio.gather(*(release(item) for item in claims), return_exceptions=True)


async def _claim_stage_b_candidates(  # noqa: PLR0913 - bounded claim context
    service: EvaluateService, run: PipelineRun, session: RunLeaseSession,
    *, explicit_ids: list[str] | None, count_inputs: bool, limit: int,
    max_days: int | None, policy: _PolicySnapshot,
) -> list[RealJobEvaluationInput]:
    """Discover and claim the complete bounded Stage B candidate set."""
    store = service._deps.store
    claimed: list[RealJobEvaluationInput] = []
    try:
        async for page in _real_id_pages(
            service, stage="b", explicit_real_ids=explicit_ids, policy=policy
        ):
            if count_inputs:
                run.evaluation_input_total += len(page)
            session.ensure_active()
            claimed.extend(await store.claim_real_job_stage_b_by_ids(
                page, stage_a_threshold=policy.config.stage_a_threshold,
                limit=limit - len(claimed), max_days=max_days,
                stage_a_policy=policy.stage_a(), stage_b_policy=policy.stage_b(),
            ))
            if len(claimed) >= limit:
                break
        return claimed
    except BaseException:
        await _release_stage_b_claims(
            store,
            claimed,
            max_concurrent=policy.config.llm.max_concurrent,
        )
        raise


async def _release_stage_b_claims(
    store: JobStore,
    claims: list[RealJobEvaluationInput],
    *,
    max_concurrent: int,
) -> None:
    """Release Stage B claims without opening one connection per claim."""
    semaphore = asyncio.Semaphore(max(1, max_concurrent))

    async def release(item: RealJobEvaluationInput) -> None:
        async with semaphore:
            await store.release_real_job_stage_b_claim(  # type: ignore[attr-defined]
                item.real_job_id,
                expected_revision=item.input_revision,
                expected_generation=item.claim_generation,
            )

    await asyncio.gather(*(release(item) for item in claims), return_exceptions=True)


async def _score_stage_b_claims(
    service: EvaluateService, run: PipelineRun, session: RunLeaseSession,
    claims: list[RealJobEvaluationInput], *, policy: _PolicySnapshot,
) -> None:
    """Score a discovered Stage B set with bounded concurrency."""
    store = service._deps.store
    semaphore = asyncio.Semaphore(max(1, policy.config.llm.max_concurrent))

    async def worker(item: RealJobEvaluationInput) -> None:
        try:
            async with semaphore:
                session.ensure_active()
                async with _maintain_real_job_stage_b_claim(store, item) as active:
                    if active:
                        await _score_b(service, run, session, item, policy=policy)
        except BaseException:
            await store.release_real_job_stage_b_claim(
                item.real_job_id, expected_revision=item.input_revision,
                expected_generation=item.claim_generation,
            )
            raise
        finally:
            run.stage_b_processed += 1
            service._emit_progress(run)

    workers = [asyncio.create_task(worker(item)) for item in claims]
    try:
        await asyncio.gather(*workers)
    except BaseException:
        for worker_task in workers:
            worker_task.cancel()
        await asyncio.gather(*workers, return_exceptions=True)
        raise


@asynccontextmanager
async def _maintain_real_job_stage_b_claim(
    store: _StageBClaimRefresher, item: RealJobEvaluationInput
) -> AsyncIterator[bool]:
    async with _maintain_real_job_claim(store, item, stage="b") as active:
        yield active


@asynccontextmanager
async def _maintain_real_job_claim(
    store: _StageBClaimRefresher, item: RealJobEvaluationInput, *, stage: str
) -> AsyncIterator[bool]:
    refresh = (
        store.refresh_real_job_stage_a_claim
        if stage == "a"
        else store.refresh_real_job_stage_b_claim
    )
    if not await refresh(
        item.real_job_id,
        expected_revision=item.input_revision,
        expected_generation=item.claim_generation,
    ):
        yield False
        return

    async def heartbeat() -> None:
        while True:
            await asyncio.sleep(STAGE_B_LEASE_HEARTBEAT_SECONDS)
            if not await refresh(
                item.real_job_id,
                expected_revision=item.input_revision,
                expected_generation=item.claim_generation,
            ):
                return

    task = asyncio.create_task(heartbeat())
    try:
        yield True
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError, Exception):
            await task


async def _score_a(
    service: EvaluateService,
    run: PipelineRun,
    session: RunLeaseSession,
    item: RealJobEvaluationInput,
    *,
    policy: _PolicySnapshot,
) -> None:
    store = service._deps.store
    real_id = item.real_job_id
    revision = item.input_revision
    bundle = service._deps.prompt_renderer.render_stage_a(
        resume_text=policy.config.resume_text, job=item.job
    )
    request = LLMRequest(messages=bundle.messages, model=policy.config.llm.stage_a)
    for attempt in range(2):
        session.ensure_active()
        if not await store.refresh_real_job_stage_a_claim(
            real_id,
            expected_revision=revision,
            expected_generation=item.claim_generation,
        ):
            return
        ledger_day = await service._budget.reserve()
        if ledger_day is None:
            await store.release_real_job_stage_a_claim(
                real_id,
                expected_revision=revision,
                expected_generation=item.claim_generation,
            )
            return
        try:
            response = await service._deps.llm_stage_a.complete(request)
            session.ensure_active()
            await record_usage(
                service._deps.store_ops,
                response,
                UsageRecordContext(item.source_job_id, "a", run.run_id, ledger_day),
            )
            run.total_llm_cost_usd += response.cost_usd or 0.0
            result = parse_stage_a_response(
                response.content,
                model=response.model,
                prompt_hash=bundle.prompt_hash,
                resume_hash=bundle.resume_hash,
                cost_usd=response.cost_usd,
            )
            if await store.save_real_job_stage_a(
                real_id,
                result,
                expected_revision=revision,
                expected_generation=item.claim_generation,
            ):
                run.stage_a_scored += 1
            return
        except ScoringParseError as error:
            if attempt == 0:
                continue
            await store.save_real_job_stage_a_error(
                real_id,
                str(error),
                expected_revision=revision,
                expected_generation=item.claim_generation,
            )
            run.errors += 1
            return
        except Exception as error:
            session.ensure_active()
            if attempt == 0:
                continue
            await store.save_real_job_stage_a_error(
                real_id,
                str(error),
                expected_revision=revision,
                expected_generation=item.claim_generation,
            )
            run.errors += 1
            return


async def _score_b(
    service: EvaluateService,
    run: PipelineRun,
    session: RunLeaseSession,
    item: RealJobEvaluationInput,
    *,
    policy: _PolicySnapshot,
) -> None:
    store = service._deps.store
    real_id = item.real_job_id
    revision = item.input_revision
    bundle = service._deps.prompt_renderer.render_stage_b(
        resume_text=policy.config.resume_text,
        job=item.job,
        stage_a_score=item.stage_a_score,
    )
    request = LLMRequest(messages=bundle.messages, model=policy.config.llm.stage_b)
    for attempt in range(2):
        session.ensure_active()
        if not await store.refresh_real_job_stage_b_claim(
            real_id,
            expected_revision=revision,
            expected_generation=item.claim_generation,
        ):
            return
        ledger_day = await service._budget.reserve()
        if ledger_day is None:
            await store.release_real_job_stage_b_claim(
                real_id,
                expected_revision=revision,
                expected_generation=item.claim_generation,
            )
            return
        try:
            response = await service._deps.llm_stage_b.complete(request)
            session.ensure_active()
            await record_usage(
                service._deps.store_ops,
                response,
                UsageRecordContext(item.source_job_id, "b", run.run_id, ledger_day),
            )
            run.total_llm_cost_usd += response.cost_usd or 0.0
            result = parse_stage_b_response(
                response.content,
                model=response.model,
                prompt_hash=bundle.prompt_hash,
                resume_hash=bundle.resume_hash,
                cost_usd=response.cost_usd,
            )
            if await store.save_real_job_stage_b(
                real_id,
                result,
                expected_revision=revision,
                expected_generation=item.claim_generation,
            ):
                run.stage_b_scored += 1
                if run.verdict_counts is not None:
                    run.verdict_counts[result.verdict.value] += 1
            return
        except ScoringParseError as error:
            if attempt == 0:
                continue
            await store.save_real_job_stage_b_error(
                real_id,
                str(error),
                expected_revision=revision,
                expected_generation=item.claim_generation,
            )
            run.errors += 1
            return
        except Exception as error:
            session.ensure_active()
            if attempt == 0:
                continue
            await store.save_real_job_stage_b_error(
                real_id,
                str(error),
                expected_revision=revision,
                expected_generation=item.claim_generation,
            )
            run.errors += 1
            return
