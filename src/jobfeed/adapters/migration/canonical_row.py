"""Versioned, backend-neutral canonical row hashing for migration parity."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Final

from jobfeed.adapters.migration._canonical_codec_v1 import (
    COLUMN_KINDS,
    SORTABLE_KINDS,
    _encode_value,
    _frame,
    _primary_key_value,
)

CODEC_V1: Final = "jobfeed-canonical-row-v1"


@dataclass(frozen=True, kw_only=True)
class CanonicalColumn:
    """One declared column in a canonical row schema."""

    name: str
    kind: str
    nullable: bool = True

    def __post_init__(self) -> None:
        """Reject ambiguous names and unsupported type interpretations."""
        if not self.name:
            raise ValueError("canonical column name must not be empty")
        if self.kind not in COLUMN_KINDS:
            raise ValueError(f"unknown canonical column kind: {self.kind!r}")


@dataclass(frozen=True, kw_only=True)
class CanonicalSchema:
    """Ordered schema and deterministic primary-key order for a row stream."""

    name: str
    columns: tuple[CanonicalColumn, ...]
    primary_key: tuple[str, ...]

    def __post_init__(self) -> None:
        """Validate that the schema completely defines a sortable row stream."""
        if not self.name:
            raise ValueError("canonical schema name must not be empty")
        if not self.columns:
            raise ValueError("canonical schema must declare at least one column")
        names = tuple(column.name for column in self.columns)
        if len(set(names)) != len(names):
            raise ValueError("canonical schema column names must be unique")
        if not self.primary_key or len(set(self.primary_key)) != len(self.primary_key):
            raise ValueError(
                "canonical schema primary key must be non-empty and unique"
            )
        by_name = {column.name: column for column in self.columns}
        missing = set(self.primary_key) - set(by_name)
        if missing:
            raise ValueError(
                f"primary-key columns missing from schema: {sorted(missing)}"
            )
        for name in self.primary_key:
            column = by_name[name]
            if column.nullable:
                raise ValueError(f"primary-key column must be non-nullable: {name}")
            if column.kind not in SORTABLE_KINDS:
                raise ValueError(
                    f"primary-key column kind is not sortable: {column.kind}"
                )


def _require_version(codec_version: str) -> None:
    if codec_version != CODEC_V1:
        raise ValueError(f"unknown canonical codec version: {codec_version!r}")


def _schema_bytes(schema: CanonicalSchema, codec_version: str) -> bytes:
    parts = [
        _frame(b"M", b"jobfeed-canonical-row"),
        _frame(b"V", codec_version.encode("ascii")),
        _frame(b"S", schema.name.encode("utf-8")),
    ]
    for column in schema.columns:
        declaration = b"".join(
            (
                _frame(b"n", column.name.encode("utf-8")),
                _frame(b"k", column.kind.encode("ascii")),
                _frame(b"q", b"1" if column.nullable else b"0"),
            )
        )
        parts.append(_frame(b"C", declaration))
    for name in schema.primary_key:
        parts.append(_frame(b"P", name.encode("utf-8")))
    return b"".join(parts)


def _prepare_row(
    schema: CanonicalSchema, row: Mapping[str, object]
) -> tuple[tuple[Any, ...], bytes]:
    expected = {column.name for column in schema.columns}
    actual = set(row)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise ValueError(
            f"row columns do not match schema; missing={missing}, extra={extra}"
        )
    by_name = {column.name: column for column in schema.columns}
    key = tuple(
        _primary_key_value(by_name[name].kind, name, row[name])
        for name in schema.primary_key
    )
    payload = b"".join(
        _encode_value(
            column.kind,
            nullable=column.nullable,
            column_name=column.name,
            value=row[column.name],
        )
        for column in schema.columns
    )
    return key, _frame(b"R", payload)


class CanonicalRowHasher:
    """Incrementally hash one globally primary-key-ordered canonical row stream."""

    def __init__(
        self, schema: CanonicalSchema, *, codec_version: str = CODEC_V1
    ) -> None:
        """Start a schema-bound versioned stream."""
        _require_version(codec_version)
        self._schema = schema
        self._hash = hashlib.sha256(_schema_bytes(schema, codec_version))
        self._last_key: tuple[Any, ...] | None = None
        self._row_count = 0

    def update_rows(self, rows: Iterable[Mapping[str, object]]) -> None:
        """Add an ordered chunk, rejecting duplicates and reversed boundaries.

        Args:
            rows: Rows ordered strictly by the schema's primary key.
        """
        for row in rows:
            self._update_prepared(*_prepare_row(self._schema, row))

    def _update_prepared(self, key: tuple[Any, ...], row_frame: bytes) -> None:
        if self._last_key is not None and key <= self._last_key:
            raise ValueError("canonical rows violate strict primary-key order")
        self._hash.update(row_frame)
        self._last_key = key
        self._row_count += 1

    def hexdigest(self) -> str:
        """Return the digest without closing or mutating the incremental stream.

        Returns:
            Lowercase SHA-256 hex digest for all rows received so far.
        """
        final = self._hash.copy()
        final.update(_frame(b"Z", str(self._row_count).encode("ascii")))
        return final.hexdigest()


def canonical_rows_sha256(
    schema: CanonicalSchema,
    rows: Iterable[Mapping[str, object]],
    *,
    codec_version: str = CODEC_V1,
) -> str:
    """Hash rows after sorting them by the schema's declared primary key.

    Args:
        schema: Version-independent ordered column and primary-key declaration.
        rows: Complete row iterable; caller order does not affect the digest.
        codec_version: Exact canonical protocol version to use.

    Returns:
        Lowercase schema-bound SHA-256 hex digest.
    """
    _require_version(codec_version)
    prepared = sorted(
        (_prepare_row(schema, row) for row in rows), key=lambda item: item[0]
    )
    hasher = CanonicalRowHasher(schema, codec_version=codec_version)
    for key, row_frame in prepared:
        hasher._update_prepared(key, row_frame)
    return hasher.hexdigest()
