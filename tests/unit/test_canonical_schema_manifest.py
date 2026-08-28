"""Alembic-0009 contracts for the versioned canonical schema manifest."""

from __future__ import annotations

import copy
import re
from pathlib import Path

import pytest

from jobfeed.adapters.migration.canonical_schema_manifest import (
    CANONICAL_ROW_SCHEMAS_V1,
    CANONICAL_SCHEMA_MANIFEST_V1,
    canonical_schema_manifest_document,
    validate_schema_manifest,
)

_ROOT = Path(__file__).resolve().parents[2]
_VERSIONS = _ROOT / "migrations" / "versions"
_TABLE_ORDER = (
    "jobs",
    "evaluations",
    "pipeline_runs",
    "resume_variants",
    "job_status",
    "job_status_history",
    "applied",
    "resume_snapshots",
    "companies",
    "cost_ledger",
    "state",
    "llm_usage",
    "interview_rounds",
    "step_timings",
)
_JSON_TEXT_COLUMNS = {
    ("jobs", "domain_tags"),
    ("jobs", "tech_required"),
}
_INTEGER_BOOL_COLUMNS = {
    ("jobs", "clearance_required"),
    ("jobs", "school_restricted"),
    ("jobs", "is_swe_role"),
    ("companies", "ats_override"),
}


def _split_columns(body: str) -> list[str]:
    parts: list[str] = []
    depth = 0
    start = 0
    for index, character in enumerate(body):
        if character == "(":
            depth += 1
        elif character == ")":
            depth -= 1
        elif character == "," and depth == 0:
            parts.append(body[start:index].strip())
            start = index + 1
    parts.append(body[start:].strip())
    return parts


def _source_type(definition: str) -> str:
    upper = definition.upper()
    candidates = (
        ("DOUBLE PRECISION", "double precision"),
        ("TIMESTAMPTZ", "timestamp with time zone"),
        ("SERIAL", "integer"),
        ("INTEGER", "integer"),
        ("JSONB", "jsonb"),
        ("BOOLEAN", "boolean"),
        ("REAL", "real"),
        ("TEXT", "text"),
    )
    for prefix, normalized in candidates:
        if upper.startswith(prefix):
            return normalized
    raise AssertionError(f"unparsed Alembic column type: {definition}")


def _create_table_columns() -> dict[str, list[dict[str, object]]]:
    tables: dict[str, list[dict[str, object]]] = {}
    for filename in (
        "0001_initial_schema.py",
        "0003_llm_usage.py",
        "0006_phase6_status_apply.py",
        "0007_phase9_observability.py",
    ):
        text = (_VERSIONS / filename).read_text("utf-8")
        for match in re.finditer(
            r"CREATE TABLE (\w+) \((.*?)\n    \)", text, flags=re.DOTALL
        ):
            columns: list[dict[str, object]] = []
            for definition in _split_columns(match.group(2)):
                if definition.upper().startswith(("UNIQUE", "CONSTRAINT", "CHECK")):
                    continue
                name, remainder = definition.split(maxsplit=1)
                upper = remainder.upper()
                columns.append(
                    {
                        "name": name,
                        "source_sql_type": _source_type(remainder),
                        "nullable": not ("NOT NULL" in upper or "PRIMARY KEY" in upper),
                        "primary_key": "PRIMARY KEY" in upper,
                    }
                )
            tables[match.group(1)] = columns
    tables["jobs"].append(
        {
            "name": "closed_at",
            "source_sql_type": "timestamp with time zone",
            "nullable": True,
            "primary_key": False,
        }
    )
    tables["pipeline_runs"].extend(
        [
            {
                "name": "status",
                "source_sql_type": "text",
                "nullable": False,
                "primary_key": False,
            },
            {
                "name": "jobs_gate_passed",
                "source_sql_type": "integer",
                "nullable": False,
                "primary_key": False,
            },
            {
                "name": "jobs_seniority_filtered",
                "source_sql_type": "integer",
                "nullable": False,
                "primary_key": False,
            },
        ]
    )
    return tables


def _kind(table: str, column: str, source_type: str) -> str:
    if source_type in {"double precision", "real"}:
        return "float"
    if source_type == "timestamp with time zone":
        return "timestamp"
    if source_type == "jsonb" or (table, column) in _JSON_TEXT_COLUMNS:
        return "json"
    if source_type == "boolean" or (table, column) in _INTEGER_BOOL_COLUMNS:
        return "bool"
    if source_type == "integer":
        return "int"
    return "text"


def _target_type(kind: str) -> str:
    if kind in {"bool", "int"}:
        return "INTEGER"
    if kind == "float":
        return "REAL"
    return "TEXT"


def test_manifest_matches_every_alembic_0008_table_and_column() -> None:
    """The registry exactly covers 0009 order, PK, type, kind, and nullability."""
    alembic = _create_table_columns()
    manifest = CANONICAL_SCHEMA_MANIFEST_V1

    assert manifest.alembic_revision == "0009"
    assert tuple(table.name for table in manifest.tables) == _TABLE_ORDER
    assert tuple(schema.name for schema in CANONICAL_ROW_SCHEMAS_V1) == tuple(
        f"{name}-v1" for name in _TABLE_ORDER
    )
    assert set(alembic) == set(_TABLE_ORDER)
    for table in manifest.tables:
        expected = alembic[table.name]
        assert table.primary_key == tuple(
            str(column["name"]) for column in expected if column["primary_key"]
        )
        assert len(table.columns) == len(expected)
        for actual, source in zip(table.columns, expected, strict=True):
            kind = _kind(
                table.name, str(source["name"]), str(source["source_sql_type"])
            )
            assert actual.name == source["name"]
            assert actual.source_sql_type == source["source_sql_type"]
            assert actual.target_sqlite_type == _target_type(kind)
            assert actual.codec_kind == kind
            assert actual.nullable is source["nullable"]


def test_bundled_manifest_validates_without_inference() -> None:
    """The serialized source of truth parses to the exported immutable registry."""
    document = canonical_schema_manifest_document()

    assert validate_schema_manifest(document) == CANONICAL_SCHEMA_MANIFEST_V1


def test_manifest_unknown_versions_fail_closed() -> None:
    """Manifest and codec versions are exact gates, not compatibility guesses."""
    for key, value in (
        ("manifest_version", 2),
        ("canonical_row_codec_version", "future-v2"),
        ("alembic_revision", "0007"),
    ):
        document = canonical_schema_manifest_document()
        document[key] = value
        with pytest.raises(ValueError, match=r"version|revision"):
            validate_schema_manifest(document)


def _manifest_tables(document: dict[str, object]) -> list[object]:
    tables = document["tables"]
    assert isinstance(tables, list)
    return tables


def _first_table_columns(document: dict[str, object]) -> list[object]:
    table = _manifest_tables(document)[0]
    assert isinstance(table, dict)
    columns = table["columns"]
    assert isinstance(columns, list)
    return columns


def test_manifest_schema_mismatches_fail_closed() -> None:
    """Order, PK, column, type, kind, and nullability drift are all rejected."""
    table_order = copy.deepcopy(canonical_schema_manifest_document())
    tables = _manifest_tables(table_order)
    tables[0], tables[1] = tables[1], tables[0]

    missing_table = copy.deepcopy(canonical_schema_manifest_document())
    _manifest_tables(missing_table).pop()

    extra_table = copy.deepcopy(canonical_schema_manifest_document())
    tables = _manifest_tables(extra_table)
    tables.append(copy.deepcopy(tables[-1]))

    duplicate_table = copy.deepcopy(canonical_schema_manifest_document())
    tables = _manifest_tables(duplicate_table)
    tables[1] = copy.deepcopy(tables[0])

    primary_key = copy.deepcopy(canonical_schema_manifest_document())
    table = _manifest_tables(primary_key)[0]
    assert isinstance(table, dict)
    table["primary_key"] = ["canonical_id"]

    column_order = copy.deepcopy(canonical_schema_manifest_document())
    columns = _first_table_columns(column_order)
    columns[0], columns[1] = columns[1], columns[0]

    missing_column = copy.deepcopy(canonical_schema_manifest_document())
    _first_table_columns(missing_column).pop()

    extra_column = copy.deepcopy(canonical_schema_manifest_document())
    columns = _first_table_columns(extra_column)
    extra = copy.deepcopy(columns[-1])
    assert isinstance(extra, dict)
    extra["name"] = "unexpected"
    columns.append(extra)

    duplicate_column = copy.deepcopy(canonical_schema_manifest_document())
    columns = _first_table_columns(duplicate_column)
    columns[1] = copy.deepcopy(columns[0])

    column_shape = copy.deepcopy(canonical_schema_manifest_document())
    column = _first_table_columns(column_shape)[0]
    assert isinstance(column, dict)
    column["nullable"] = True

    candidates = (
        table_order,
        missing_table,
        extra_table,
        duplicate_table,
        primary_key,
        column_order,
        missing_column,
        extra_column,
        duplicate_column,
        column_shape,
    )

    for document in candidates:
        with pytest.raises(ValueError, match="mismatch"):
            validate_schema_manifest(document)


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("source_sql_type", "bigint"),
        ("target_sqlite_type", "BLOB"),
        ("codec_kind", "text"),
    ],
)
def test_manifest_column_type_mismatches_fail_closed(
    field: str, replacement: object
) -> None:
    """No source, target, or codec type may be inferred from a nearby column."""
    document = copy.deepcopy(canonical_schema_manifest_document())
    tables = document["tables"]
    assert isinstance(tables, list)
    table = tables[0]
    assert isinstance(table, dict)
    columns = table["columns"]
    assert isinstance(columns, list)
    column = columns[0]
    assert isinstance(column, dict)
    column[field] = replacement

    with pytest.raises(ValueError, match="mismatch"):
        validate_schema_manifest(document)
