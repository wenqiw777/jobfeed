"""Golden and failure contracts for canonical migration-row codec v1."""

from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from jobfeed.adapters.migration.canonical_row import (
    CODEC_V1,
    CanonicalColumn,
    CanonicalRowHasher,
    CanonicalSchema,
    canonical_rows_sha256,
)

_FIXTURE = (
    Path(__file__).resolve().parents[1] / "fixtures" / "canonical_row_v1_golden.json"
)


def _load_golden() -> tuple[CanonicalSchema, list[dict[str, object]], str]:
    fixture: dict[str, Any] = json.loads(_FIXTURE.read_text("utf-8"))
    schema_data = fixture["schema"]
    schema = CanonicalSchema(
        name=schema_data["name"],
        primary_key=tuple(schema_data["primary_key"]),
        columns=tuple(CanonicalColumn(**column) for column in schema_data["columns"]),
    )
    kinds = {column.name: column.kind for column in schema.columns}
    rows: list[dict[str, object]] = []
    for fixture_row in fixture["rows"]:
        row = dict(fixture_row)
        for name, kind in kinds.items():
            if kind == "decimal" and row[name] is not None:
                row[name] = Decimal(str(row[name]))
        rows.append(row)
    assert fixture["codec_version"] == CODEC_V1
    return schema, rows, fixture["expected_sha256"]


def _value_schema(kind: str) -> CanonicalSchema:
    return CanonicalSchema(
        name="single_value",
        primary_key=("id",),
        columns=(
            CanonicalColumn(name="id", kind="int", nullable=False),
            CanonicalColumn(name="value", kind=kind, nullable=True),
        ),
    )


def test_mixed_type_golden_sha_is_stable() -> None:
    """Fixture order does not affect the versioned, PK-ordered golden digest."""
    schema, rows, expected = _load_golden()

    assert canonical_rows_sha256(schema, rows) == expected
    assert canonical_rows_sha256(schema, reversed(rows)) == expected


def test_ordered_chunk_boundaries_do_not_change_digest() -> None:
    """The same globally PK-ordered rows hash identically across chunk splits."""
    schema, rows, expected = _load_golden()
    ordered = sorted(rows, key=lambda row: int(row["id"]))

    one_by_one = CanonicalRowHasher(schema)
    for row in ordered:
        one_by_one.update_rows([row])
    all_at_once = CanonicalRowHasher(schema)
    all_at_once.update_rows(ordered)

    assert one_by_one.hexdigest() == expected
    assert all_at_once.hexdigest() == expected


def test_length_framing_prevents_field_boundary_collision() -> None:
    """Visually concatenated text fields cannot collide across boundaries."""
    schema = CanonicalSchema(
        name="boundary",
        primary_key=("id",),
        columns=(
            CanonicalColumn(name="id", kind="int", nullable=False),
            CanonicalColumn(name="left", kind="text", nullable=False),
            CanonicalColumn(name="right", kind="text", nullable=False),
        ),
    )

    first = canonical_rows_sha256(schema, [{"id": 1, "left": "ab", "right": "c"}])
    second = canonical_rows_sha256(schema, [{"id": 1, "left": "a", "right": "bc"}])

    assert first != second


def test_type_tags_keep_lookalike_values_distinct() -> None:
    """Bool, int, decimal, float, text, JSON, empty, and NULL stay distinct."""
    cases = [
        ("bool", True),
        ("int", 1),
        ("decimal", Decimal("1")),
        ("float", 1.0),
        ("text", "1"),
        ("json", "1"),
        ("text", ""),
        ("text", None),
    ]
    digests = {
        canonical_rows_sha256(_value_schema(kind), [{"id": 1, "value": value}])
        for kind, value in cases
    }

    assert len(digests) == len(cases)


@pytest.mark.parametrize(
    ("kind", "left", "right"),
    [
        ("decimal", Decimal("123.4500"), Decimal("123.45")),
        (
            "timestamp",
            "2026-08-11T01:02:03.123456-04:00",
            "2026-08-11T05:02:03.123456Z",
        ),
        ("json", '{"z":2,"a":1}', {"a": 1, "z": 2}),
    ],
)
def test_backend_representations_have_one_semantic_encoding(
    kind: str, left: object, right: object
) -> None:
    """PG/SQLite representations converge for decimals, UTC, and JSON."""
    schema = _value_schema(kind)

    assert canonical_rows_sha256(schema, [{"id": 1, "value": left}]) == (
        canonical_rows_sha256(schema, [{"id": 1, "value": right}])
    )


@pytest.mark.parametrize(
    ("kind", "value"),
    [
        ("float", float("nan")),
        ("float", float("inf")),
        ("float", float("-inf")),
        ("decimal", Decimal("NaN")),
        ("decimal", Decimal("Infinity")),
        ("json", {"bad": float("nan")}),
    ],
)
def test_nonfinite_values_fail_closed(kind: str, value: object) -> None:
    """Non-finite numeric values never enter a supposedly canonical stream."""
    with pytest.raises(ValueError, match="finite"):
        canonical_rows_sha256(_value_schema(kind), [{"id": 1, "value": value}])


@pytest.mark.parametrize(
    "value",
    [datetime(2026, 8, 11, 1, 2, 3), "2026-08-11T01:02:03.000000"],
)
def test_naive_timestamps_fail_closed(value: object) -> None:
    """Datetime objects and strings both require an explicit UTC offset."""
    with pytest.raises(ValueError, match="timezone-aware"):
        canonical_rows_sha256(_value_schema("timestamp"), [{"id": 1, "value": value}])


def test_unknown_codec_and_kind_fail_closed() -> None:
    """Unknown protocol versions or column kinds are never guessed."""
    schema = _value_schema("text")
    with pytest.raises(ValueError, match="codec version"):
        canonical_rows_sha256(
            schema, [{"id": 1, "value": "x"}], codec_version="future-v2"
        )
    with pytest.raises(ValueError, match="column kind"):
        CanonicalColumn(name="value", kind="bytes")


def test_schema_shape_and_pk_stream_order_fail_closed() -> None:
    """Missing/extra columns, duplicate PKs, and reversed chunks are rejected."""
    schema = _value_schema("text")
    with pytest.raises(ValueError, match="row columns"):
        canonical_rows_sha256(schema, [{"id": 1}])
    with pytest.raises(ValueError, match="row columns"):
        canonical_rows_sha256(schema, [{"id": 1, "value": "x", "extra": 2}])

    hasher = CanonicalRowHasher(schema)
    hasher.update_rows([{"id": 2, "value": "two"}])
    with pytest.raises(ValueError, match="primary-key order"):
        hasher.update_rows([{"id": 1, "value": "one"}])


def test_schema_column_order_is_part_of_digest() -> None:
    """Changing declared column order changes the schema-bound digest."""
    first = CanonicalSchema(
        name="order",
        primary_key=("id",),
        columns=(
            CanonicalColumn(name="id", kind="int", nullable=False),
            CanonicalColumn(name="a", kind="text"),
            CanonicalColumn(name="b", kind="text"),
        ),
    )
    second = CanonicalSchema(
        name="order",
        primary_key=("id",),
        columns=(first.columns[0], first.columns[2], first.columns[1]),
    )
    row = {"id": 1, "a": "x", "b": "y"}

    assert canonical_rows_sha256(first, [row]) != canonical_rows_sha256(second, [row])
