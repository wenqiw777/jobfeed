"""SQLite Stage B row mapping helpers with raw JSON validation."""

from __future__ import annotations

import json
from typing import cast

from jobfeed.adapters.store.sqlite_row import (
    RowData,
    optional_float,
    required_int,
    required_object,
    required_str,
)
from jobfeed.domain.models import (
    FitAnalysis,
    GapItem,
    MatchItem,
    StageBResult,
    Verdict,
)
from jobfeed.domain.types import VALID_SEVERITIES, Severity


def stage_b_from_row(row: RowData) -> StageBResult | None:
    """Build a Stage B result when a row contains completed Stage B data.

    Args:
        row: Evaluation row data.

    Returns:
        Stage B result for completed rows; otherwise None.
    """
    if row.get("stage_b_status") != "completed":
        return None
    raw_blocks = _raw_blocks_from_row(row)
    required_object(raw_blocks, "block_a")
    required_object(raw_blocks, "block_b")
    block_c = required_object(raw_blocks, "block_c")
    block_e = required_object(raw_blocks, "block_e")
    return StageBResult(
        verdict=Verdict(required_str(row, "stage_b_verdict")),
        jd_summary=required_str(row, "stage_b_jd_summary"),
        fit_analysis=FitAnalysis(
            score=required_int(block_c, "score_0_100"),
            strengths=_match_items(_required_list(block_c, "strong_match")),
            gaps=_gap_items(_required_list(block_c, "gaps")),
        ),
        resume_hooks=_string_items(_required_list(block_e, "hooks"), "hooks"),
        model=required_str(row, "stage_b_model"),
        prompt_hash=required_str(row, "stage_b_prompt_hash"),
        resume_hash=required_str(row, "stage_b_resume_hash"),
        cost_usd=optional_float(row, "stage_b_cost_usd"),
        raw_blocks=raw_blocks,
    )


def _raw_blocks_from_row(row: RowData) -> dict[str, object]:
    return {
        "block_a": _json_object(row, "block_a_json"),
        "block_b": _json_object(row, "block_b_json"),
        "block_c": _json_object(row, "block_c_json"),
        "block_e": _json_object(row, "block_e_json"),
    }


def _json_object(row: RowData, key: str) -> RowData:
    value = required_str(row, key)
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise ValueError(f"invalid JSON object column: {key}")
    return cast(RowData, parsed)


def _match_items(items: list[object]) -> list[MatchItem]:
    matches: list[MatchItem] = []
    for item in items:
        item_object = _required_item_object(item, "strong_match")
        matches.append(
            MatchItem(
                requirement=required_str(item_object, "requirement"),
                evidence=required_str(item_object, "evidence"),
            )
        )
    return matches


def _gap_items(items: list[object]) -> list[GapItem]:
    gaps: list[GapItem] = []
    for item in items:
        item_object = _required_item_object(item, "gaps")
        gaps.append(
            GapItem(
                requirement=required_str(item_object, "requirement"),
                severity=_required_severity(item_object),
                mitigation=required_str(item_object, "mitigation"),
            )
        )
    return gaps


def _string_items(items: list[object], field_name: str) -> list[str]:
    strings: list[str] = []
    for item in items:
        if not isinstance(item, str) or not item:
            raise ValueError(f"invalid string item in {field_name}")
        strings.append(item)
    return strings


def _required_list(row: RowData, key: str) -> list[object]:
    value = row.get(key)
    if not isinstance(value, list):
        raise ValueError(f"missing list column: {key}")
    return cast(list[object], value)


def _required_item_object(item: object, field_name: str) -> RowData:
    if not isinstance(item, dict):
        raise ValueError(f"invalid object item in {field_name}")
    return cast(RowData, item)


def _required_severity(item: RowData) -> Severity:
    severity = required_str(item, "severity")
    if severity not in VALID_SEVERITIES:
        raise ValueError(f"invalid gap severity: {severity}")
    return cast(Severity, severity)


__all__ = ["stage_b_from_row"]
