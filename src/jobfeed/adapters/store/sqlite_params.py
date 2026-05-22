"""SQLite parameter serialization helpers for domain objects."""

from __future__ import annotations

from jobfeed.adapters.store.sqlite_mapping import (
    block_json,
    datetime_to_db,
    quality_to_db,
)
from jobfeed.domain.models import JobPosting, PipelineRun, StageAResult, StageBResult


def job_id_value(job_id: str) -> int:
    """Parse a store-assigned string identity into a SQLite integer id.

    Args:
        job_id: Store-assigned job identity from the JobStore port.

    Returns:
        Integer SQLite row id.
    """
    return int(job_id)


def job_params(job: JobPosting) -> tuple[object, ...]:
    """Serialize a job posting into jobs table parameters.

    Args:
        job: Job posting to serialize.

    Returns:
        Positional parameters for insert or update statements.
    """
    return (
        job.platform,
        job.canonical_id,
        job.url,
        job.title,
        job.company,
        job.location,
        job.jd_text,
        quality_to_db(job.jd_quality),
        datetime_to_db(job.posted_at),
        datetime_to_db(job.discovered_at),
        datetime_to_db(job.enriched_at),
        job.enrich_source,
    )


def stage_a_params(job_id: str, result: StageAResult) -> tuple[object, ...]:
    """Serialize a Stage A result into evaluation parameters.

    Args:
        job_id: Store-assigned job identity.
        result: Stage A result to serialize.

    Returns:
        Positional parameters for the Stage A upsert.
    """
    return (
        job_id_value(job_id),
        result.score,
        result.one_line,
        result.timing_eligible,
        result.model,
        result.cost_usd,
        result.prompt_hash,
        result.resume_hash,
    )


def stage_b_params(job_id: str, result: StageBResult) -> tuple[object, ...]:
    """Serialize a Stage B result into evaluation parameters.

    Args:
        job_id: Store-assigned job identity.
        result: Stage B result to serialize.

    Returns:
        Positional parameters for the Stage B upsert.
    """
    return (
        job_id_value(job_id),
        result.verdict.value,
        result.jd_summary,
        block_json(result.raw_blocks, "block_a"),
        block_json(result.raw_blocks, "block_b"),
        block_json(result.raw_blocks, "block_c"),
        block_json(result.raw_blocks, "block_e"),
        result.model,
        result.cost_usd,
        result.prompt_hash,
        result.resume_hash,
    )


def pipeline_run_params(run: PipelineRun) -> tuple[object, ...]:
    """Serialize a pipeline run into pipeline_runs parameters.

    Args:
        run: Pipeline run to serialize.

    Returns:
        Positional parameters for insert.
    """
    return (
        run.run_id,
        datetime_to_db(run.started_at),
        run.source,
        run.jobs_discovered,
        run.jobs_inserted,
        run.jobs_updated,
        run.jobs_filtered,
        run.jobs_ml_gated,
        run.stage_a_scored,
        run.stage_b_scored,
        run.jobs_scored,
        run.total_llm_cost_usd,
        run.errors,
        datetime_to_db(run.finished_at),
    )


__all__ = [
    "job_id_value",
    "job_params",
    "pipeline_run_params",
    "stage_a_params",
    "stage_b_params",
]
