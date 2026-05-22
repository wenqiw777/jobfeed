"""Shared SQLite row validation helpers."""

from __future__ import annotations

from typing import cast

RowData = dict[str, object]


def required_str(row: RowData, key: str) -> str:
    """Read a required string column.

    Args:
        row: SQLite row data.
        key: Column name to read.

    Returns:
        String column value.

    Raises:
        ValueError: If the column is missing or not a string.
    """
    value = row.get(key)
    if not isinstance(value, str):
        raise ValueError(f"missing string column: {key}")
    return value


def required_int(row: RowData, key: str) -> int:
    """Read a required integer column.

    Args:
        row: SQLite row data.
        key: Column name to read.

    Returns:
        Integer column value.

    Raises:
        ValueError: If the column is missing, bool, or not an integer.
    """
    value = row.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"missing integer column: {key}")
    return value


def required_float(row: RowData, key: str) -> float:
    """Read a required numeric column as float.

    Args:
        row: SQLite row data.
        key: Column name to read.

    Returns:
        Numeric column value as float.

    Raises:
        ValueError: If the column is missing or not numeric.
    """
    value = row.get(key)
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"missing float column: {key}")
    return float(value)


def optional_float(row: RowData, key: str) -> float | None:
    """Read an optional numeric column as float.

    Args:
        row: SQLite row data.
        key: Column name to read.

    Returns:
        Numeric column value as float, or None when absent.

    Raises:
        ValueError: If the column is present and not numeric.
    """
    value = row.get(key)
    if value is None:
        return None
    if not isinstance(value, bool) and isinstance(value, int | float):
        return float(value)
    raise ValueError(f"invalid float column: {key}")


def required_object(row: RowData, key: str) -> RowData:
    """Read a required object field from parsed JSON.

    Args:
        row: Parsed JSON object.
        key: Field name to read.

    Returns:
        Nested JSON object.

    Raises:
        ValueError: If the field is missing or not an object.
    """
    value = row.get(key)
    if not isinstance(value, dict):
        raise ValueError(f"missing object column: {key}")
    return cast(RowData, value)


__all__ = [
    "RowData",
    "optional_float",
    "required_float",
    "required_int",
    "required_object",
    "required_str",
]
