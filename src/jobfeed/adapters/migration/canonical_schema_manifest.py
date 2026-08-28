"""Executable Alembic-0009 schema contract for migration parity."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final, cast

from jobfeed.adapters.migration.canonical_row import (
    CODEC_V1,
    CanonicalColumn,
    CanonicalSchema,
)

MANIFEST_VERSION_V1: Final = 1
ALEMBIC_REVISION_V1: Final = "0009"
MIGRATED_TABLE_ORDER_V1: Final = (
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
_MANIFEST_FILE = Path(__file__).with_name("canonical_schema_manifest_v1.json")
_ROOT_KEYS = {
    "manifest_version",
    "canonical_row_codec_version",
    "alembic_revision",
    "tables",
}
_TABLE_KEYS = {"name", "primary_key", "columns"}
_COLUMN_KEYS = {
    "name",
    "source_sql_type",
    "target_sqlite_type",
    "codec_kind",
    "nullable",
}


@dataclass(frozen=True, kw_only=True)
class CanonicalManifestColumn:
    """One source-to-target column mapping in the v1 schema manifest."""

    name: str
    source_sql_type: str
    target_sqlite_type: str
    codec_kind: str
    nullable: bool


@dataclass(frozen=True, kw_only=True)
class CanonicalManifestTable:
    """One ordered table schema and primary key in the v1 manifest."""

    name: str
    primary_key: tuple[str, ...]
    columns: tuple[CanonicalManifestColumn, ...]


@dataclass(frozen=True, kw_only=True)
class CanonicalSchemaManifest:
    """Immutable versioned schema contract for all migrated tables."""

    manifest_version: int
    canonical_row_codec_version: str
    alembic_revision: str
    tables: tuple[CanonicalManifestTable, ...]


def _mapping(value: object, path: str) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ValueError(f"schema manifest mismatch at {path}: expected object")
    return cast(dict[str, object], value)


def _exact_keys(value: Mapping[str, object], expected: set[str], path: str) -> None:
    actual = set(value)
    if actual != expected:
        raise ValueError(
            f"schema manifest mismatch at {path}: "
            f"missing={sorted(expected - actual)}, extra={sorted(actual - expected)}"
        )


def _string(value: object, path: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"schema manifest mismatch at {path}: expected string")
    return value


def _strings(value: object, path: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ValueError(f"schema manifest mismatch at {path}: expected list")
    result = tuple(_string(item, path) for item in value)
    if len(set(result)) != len(result):
        raise ValueError(f"schema manifest mismatch at {path}: duplicate values")
    return result


def _parse_column(value: object, path: str) -> CanonicalManifestColumn:
    data = _mapping(value, path)
    _exact_keys(data, _COLUMN_KEYS, path)
    nullable = data["nullable"]
    if type(nullable) is not bool:
        raise ValueError(f"schema manifest mismatch at {path}.nullable")
    column = CanonicalManifestColumn(
        name=_string(data["name"], f"{path}.name"),
        source_sql_type=_string(data["source_sql_type"], f"{path}.source_sql_type"),
        target_sqlite_type=_string(
            data["target_sqlite_type"], f"{path}.target_sqlite_type"
        ),
        codec_kind=_string(data["codec_kind"], f"{path}.codec_kind"),
        nullable=nullable,
    )
    CanonicalColumn(name=column.name, kind=column.codec_kind, nullable=column.nullable)
    return column


def _parse_table(value: object, index: int) -> CanonicalManifestTable:
    path = f"tables[{index}]"
    data = _mapping(value, path)
    _exact_keys(data, _TABLE_KEYS, path)
    raw_columns = data["columns"]
    if not isinstance(raw_columns, list) or not raw_columns:
        raise ValueError(f"schema manifest mismatch at {path}.columns")
    columns = tuple(
        _parse_column(column, f"{path}.columns[{column_index}]")
        for column_index, column in enumerate(raw_columns)
    )
    table = CanonicalManifestTable(
        name=_string(data["name"], f"{path}.name"),
        primary_key=_strings(data["primary_key"], f"{path}.primary_key"),
        columns=columns,
    )
    CanonicalSchema(
        name=f"{table.name}-v1",
        primary_key=table.primary_key,
        columns=tuple(
            CanonicalColumn(
                name=column.name,
                kind=column.codec_kind,
                nullable=column.nullable,
            )
            for column in columns
        ),
    )
    return table


def _parse_manifest(document: Mapping[str, object]) -> CanonicalSchemaManifest:
    _exact_keys(document, _ROOT_KEYS, "root")
    version = document["manifest_version"]
    if type(version) is not int or version != MANIFEST_VERSION_V1:
        raise ValueError(f"unknown schema manifest version: {version!r}")
    codec_version = document["canonical_row_codec_version"]
    if codec_version != CODEC_V1:
        raise ValueError(f"unknown canonical codec version: {codec_version!r}")
    revision = document["alembic_revision"]
    if revision != ALEMBIC_REVISION_V1:
        raise ValueError(f"unexpected Alembic revision: {revision!r}")
    raw_tables = document["tables"]
    if not isinstance(raw_tables, list):
        raise ValueError("schema manifest mismatch at tables: expected list")
    tables = tuple(_parse_table(table, index) for index, table in enumerate(raw_tables))
    order = tuple(table.name for table in tables)
    if order != MIGRATED_TABLE_ORDER_V1:
        raise ValueError(
            f"schema manifest table order mismatch: expected "
            f"{MIGRATED_TABLE_ORDER_V1}, got {order}"
        )
    return CanonicalSchemaManifest(
        manifest_version=version,
        canonical_row_codec_version=codec_version,
        alembic_revision=revision,
        tables=tables,
    )


def canonical_schema_manifest_document() -> dict[str, object]:
    """Load a fresh JSON document for the bundled v1 schema manifest.

    Returns:
        Mutable manifest document suitable for serialization or validation.
    """
    document = json.loads(_MANIFEST_FILE.read_text("utf-8"))
    return _mapping(document, "root")


CANONICAL_SCHEMA_MANIFEST_V1: Final = _parse_manifest(
    canonical_schema_manifest_document()
)

CANONICAL_ROW_SCHEMAS_V1: Final = tuple(
    CanonicalSchema(
        name=f"{table.name}-v1",
        primary_key=table.primary_key,
        columns=tuple(
            CanonicalColumn(
                name=column.name,
                kind=column.codec_kind,
                nullable=column.nullable,
            )
            for column in table.columns
        ),
    )
    for table in CANONICAL_SCHEMA_MANIFEST_V1.tables
)


def validate_schema_manifest(
    document: Mapping[str, object],
) -> CanonicalSchemaManifest:
    """Validate a candidate against the exact bundled v1 schema contract.

    Args:
        document: Parsed JSON manifest candidate.

    Returns:
        Immutable validated schema manifest.

    Raises:
        ValueError: If any version, table, PK, column, or type field differs.
    """
    try:
        parsed = _parse_manifest(document)
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"canonical schema manifest mismatch: {exc}") from exc
    if parsed != CANONICAL_SCHEMA_MANIFEST_V1:
        raise ValueError("canonical schema manifest mismatch from bundled v1 contract")
    return parsed
