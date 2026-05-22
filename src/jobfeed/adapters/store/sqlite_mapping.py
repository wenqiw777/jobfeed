"""SQLite row mapping helpers for Jobfeed domain objects."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import cast

from jobfeed.adapters.store.sqlite_row import (
    RowData,
    optional_float,
    required_float,
    required_int,
    required_str,
)
from jobfeed.adapters.store.sqlite_stage_b_mapping import stage_b_from_row
from jobfeed.domain.models import (
    JobEvaluation,
    JobPosting,
    PipelineRun,
    QualityBand,
    StageAResult,
)


def row_to_dict(row: sqlite3.Row) -> RowData:
    """Convert sqlite3.Row into a strictly typed dictionary.

    Args:
        row: SQLite row returned by a row-factory connection.

    Returns:
        Dictionary keyed by selected column names.
    """
    keys = list(row.keys())
    return {key: cast(object, row[key]) for key in keys}


def job_from_row(row: RowData) -> JobPosting:
    """Build a JobPosting from a jobs row.

    Args:
        row: Row containing jobs table columns.

    Returns:
        Domain job posting with string identity.
    """
    return JobPosting(
        id=str(required_int(row, "id")),
        platform=required_str(row, "platform"),
        canonical_id=required_str(row, "canonical_id"),
        url=required_str(row, "url"),
        title=required_str(row, "title"),
        company=required_str(row, "company"),
        location=required_str(row, "location"),
        discovered_at=_required_datetime(row, "discovered_at"),
        jd_text=_optional_str(row, "jd_text"),
        jd_quality=_optional_quality(row, "jd_quality"),
        posted_at=_optional_datetime(row, "posted_at"),
        enriched_at=_optional_datetime(row, "enriched_at"),
        enrich_source=_optional_str(row, "enrich_source"),
    )


def evaluation_from_row(row: RowData) -> JobEvaluation:
    """Build a joined JobEvaluation from jobs and evaluations columns.

    Args:
        row: Joined row containing jobs plus evaluation columns.

    Returns:
        Domain evaluation with optional Stage A and Stage B results.
    """
    return JobEvaluation(
        job=job_from_row(row),
        stage_a=stage_a_from_row(row),
        stage_b=stage_b_from_row(row),
    )


def stage_a_from_row(row: RowData) -> StageAResult | None:
    """Build a Stage A result when the row contains completed Stage A data.

    Args:
        row: Evaluation row data.

    Returns:
        Stage A result for completed rows; otherwise None.
    """
    if row.get("stage_a_status") != "completed":
        return None
    return StageAResult(
        score=required_int(row, "stage_a_score"),
        one_line=required_str(row, "stage_a_one_line"),
        timing_eligible=required_str(row, "stage_a_timing_eligible"),
        model=required_str(row, "stage_a_model"),
        prompt_hash=required_str(row, "stage_a_prompt_hash"),
        resume_hash=required_str(row, "stage_a_resume_hash"),
        cost_usd=optional_float(row, "stage_a_cost_usd"),
    )


def pipeline_run_from_row(row: RowData) -> PipelineRun:
    """Build a PipelineRun from a pipeline_runs row.

    Args:
        row: Pipeline run row data.

    Returns:
        Domain pipeline run.
    """
    return PipelineRun(
        run_id=required_str(row, "run_id"),
        started_at=_required_datetime(row, "started_at"),
        source=required_str(row, "source"),
        jobs_discovered=required_int(row, "jobs_discovered"),
        jobs_inserted=required_int(row, "jobs_inserted"),
        jobs_updated=required_int(row, "jobs_updated"),
        jobs_filtered=required_int(row, "jobs_filtered"),
        jobs_ml_gated=required_int(row, "jobs_ml_gated"),
        stage_a_scored=required_int(row, "stage_a_scored"),
        stage_b_scored=required_int(row, "stage_b_scored"),
        jobs_scored=required_int(row, "jobs_scored"),
        total_llm_cost_usd=required_float(row, "total_llm_cost_usd"),
        errors=required_int(row, "errors"),
        finished_at=_optional_datetime(row, "finished_at"),
    )


def datetime_to_db(value: datetime | None) -> str | None:
    """Serialize an optional datetime for SQLite.

    Args:
        value: Datetime to serialize.

    Returns:
        ISO-8601 string, or None for absent values.
    """
    return value.isoformat() if value is not None else None


def quality_to_db(value: QualityBand | None) -> str | None:
    """Serialize an optional quality band for SQLite.

    Args:
        value: Quality band to serialize.

    Returns:
        Enum value, or None for absent values.
    """
    return value.value if value is not None else None


def block_json(raw_blocks: dict[str, object] | None, key: str) -> str | None:
    """Serialize one Stage B raw block for audit storage.

    Args:
        raw_blocks: Parsed Stage B raw block object.
        key: Block key to serialize.

    Returns:
        JSON string for the block, or None when absent.
    """
    if raw_blocks is None or key not in raw_blocks:
        return None
    return json.dumps(raw_blocks[key], sort_keys=True)


def _optional_str(row: RowData, key: str) -> str | None:
    value = row.get(key)
    if value is None or isinstance(value, str):
        return value
    raise ValueError(f"invalid string column: {key}")


def _required_datetime(row: RowData, key: str) -> datetime:
    return datetime.fromisoformat(required_str(row, key))


def _optional_datetime(row: RowData, key: str) -> datetime | None:
    value = _optional_str(row, key)
    return datetime.fromisoformat(value) if value is not None else None


def _optional_quality(row: RowData, key: str) -> QualityBand | None:
    value = _optional_str(row, key)
    return QualityBand(value) if value is not None else None
