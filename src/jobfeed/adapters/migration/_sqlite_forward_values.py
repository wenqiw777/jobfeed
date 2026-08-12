"""Canonical PostgreSQL-value conversion for the SQLite migration target."""

from __future__ import annotations

import math
from collections.abc import Callable
from decimal import Decimal
from typing import Final

from jobfeed.adapters.migration._canonical_codec_v1 import (
    _json_bytes,
    _timestamp,
)
from jobfeed.adapters.migration.canonical_schema_manifest import (
    CanonicalManifestColumn,
)

_UTC_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"


def _bool(value: object) -> int:
    if type(value) is bool:
        return int(value)
    if type(value) is int and value in (0, 1):
        return value
    raise TypeError("boolean migration value must be bool or exact integer 0/1")


def _float(value: object) -> float:
    if type(value) is not float or not math.isfinite(value):
        raise TypeError("float migration value must be finite float")
    return 0.0 if value == 0.0 else value


def _integer(value: object) -> int:
    if type(value) is not int:
        raise TypeError("integer migration value must be exact int")
    return value


def _text(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError("text migration value must be str")
    return value


def _decimal(value: object) -> str:
    if not isinstance(value, Decimal):
        raise TypeError("decimal migration value must be Decimal")
    if not value.is_finite():
        raise ValueError("decimal migration value must be finite")
    return str(value)


_CONVERTERS: Final[dict[str, Callable[[object], object]]] = {
    "timestamp": lambda value: _timestamp(value).strftime(_UTC_FORMAT),
    "json": lambda value: _json_bytes(value).decode("utf-8"),
    "bool": _bool,
    "float": _float,
    "int": _integer,
    "text": _text,
    "decimal": _decimal,
}


def _sqlite_value(column: CanonicalManifestColumn, value: object) -> object:
    """Convert one already codec-validated PostgreSQL scalar for SQLite."""
    return None if value is None else _CONVERTERS[column.codec_kind](value)
