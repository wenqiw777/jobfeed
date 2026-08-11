"""Scalar encoders for canonical migration-row codec v1."""

from __future__ import annotations

import json
import math
import struct
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Final

COLUMN_KINDS: Final = frozenset(
    {"bool", "int", "decimal", "float", "timestamp", "text", "json"}
)
SORTABLE_KINDS: Final = COLUMN_KINDS - {"json"}
_FRAME_LENGTH = struct.Struct(">Q")


def _frame(tag: bytes, payload: bytes) -> bytes:
    """Frame one item so neither values nor field boundaries can collide."""
    if len(tag) != 1:
        raise ValueError("canonical frame tags must be exactly one byte")
    return tag + _FRAME_LENGTH.pack(len(payload)) + payload


def _decimal_bytes(value: object) -> bytes:
    if not isinstance(value, Decimal):
        raise TypeError("decimal column requires Decimal")
    if not value.is_finite():
        raise ValueError("decimal values must be finite")
    sign, raw_digits, raw_exponent = value.as_tuple()
    if not isinstance(raw_exponent, int):
        raise ValueError("decimal values must be finite")
    exponent = raw_exponent
    digits = list(raw_digits)
    if not any(digits):
        return b"0:0"
    while digits[-1] == 0:
        digits.pop()
        exponent += 1
    coefficient = "".join(str(digit) for digit in digits)
    prefix = "-" if sign else "+"
    return f"{prefix}{coefficient}:{exponent}".encode("ascii")


def _timestamp(value: object) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        text = value[:-1] + "+00:00" if value.endswith("Z") else value
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError as exc:
            raise ValueError(
                "timestamp must be a valid timezone-aware ISO value"
            ) from exc
    else:
        raise TypeError("timestamp column requires datetime or ISO text")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return parsed.astimezone(UTC)


def _json_bytes(value: object) -> bytes:
    try:
        parsed = (
            json.loads(
                value,
                parse_float=Decimal,
                parse_constant=_reject_json_constant,
            )
            if isinstance(value, (str, bytes, bytearray))
            else value
        )
        encoded = _canonical_json(parsed)
    except ValueError as exc:
        raise ValueError(
            "JSON numeric values must be finite and JSON must be valid"
        ) from exc
    except (TypeError, UnicodeDecodeError) as exc:
        raise ValueError("JSON value must be valid and canonically encodable") from exc
    return encoded.encode("utf-8")


def _reject_json_constant(value: str) -> object:
    raise ValueError(f"JSON numeric values must be finite: {value}")


def _json_decimal(value: Decimal) -> str:
    if not value.is_finite():
        raise ValueError("JSON numeric values must be finite")
    sign, raw_digits, raw_exponent = value.as_tuple()
    if not isinstance(raw_exponent, int):
        raise ValueError("JSON numeric values must be finite")
    digits = list(raw_digits)
    if not any(digits):
        return "0.0e0"
    exponent = raw_exponent
    while digits[-1] == 0:
        digits.pop()
        exponent += 1
    coefficient = "".join(str(digit) for digit in digits)
    mantissa = f"{coefficient[0]}.{coefficient[1:] or '0'}"
    scientific_exponent = exponent + len(coefficient) - 1
    prefix = "-" if sign else ""
    return f"{prefix}{mantissa}e{scientific_exponent}"


def _canonical_json_object(value: dict[object, object]) -> str:
    if not all(isinstance(key, str) for key in value):
        raise TypeError("JSON object keys must be strings")
    keys = (key for key in value if isinstance(key, str))
    items = (
        f"{json.dumps(key, ensure_ascii=False)}:{_canonical_json(value[key])}"
        for key in sorted(keys)
    )
    return "{" + ",".join(items) + "}"


def _canonical_json(value: object) -> str:
    if value is None:
        return "null"
    if type(value) is bool:
        return "true" if value else "false"
    if type(value) is int:
        return str(value)
    if isinstance(value, Decimal):
        return _json_decimal(value)
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError("JSON numeric values must be finite")
        return _json_decimal(Decimal(str(value)))
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(_canonical_json(item) for item in value) + "]"
    if isinstance(value, dict):
        return _canonical_json_object(value)
    raise TypeError(f"unsupported JSON value type: {type(value).__name__}")


def _bool_bytes(value: object) -> bytes:
    if type(value) is bool:
        return b"1" if value else b"0"
    if type(value) is int and value in (0, 1):
        return str(value).encode("ascii")
    raise TypeError("bool column requires bool or exact SQLite integer 0/1")


def _int_bytes(value: object) -> bytes:
    if type(value) is not int:
        raise TypeError("int column requires int")
    return str(value).encode("ascii")


def _float_bytes(value: object) -> bytes:
    if type(value) is not float:
        raise TypeError("float column requires float")
    if not math.isfinite(value):
        raise ValueError("float values must be finite")
    canonical = 0.0 if value == 0.0 else value
    return canonical.hex().encode("ascii")


def _timestamp_bytes(value: object) -> bytes:
    return _timestamp(value).strftime("%Y-%m-%dT%H:%M:%S.%fZ").encode("ascii")


def _text_bytes(value: object) -> bytes:
    if not isinstance(value, str):
        raise TypeError("text column requires str")
    return value.encode("utf-8")


_VALUE_ENCODERS: Final[dict[str, tuple[bytes, Callable[[object], bytes]]]] = {
    "bool": (b"B", _bool_bytes),
    "int": (b"I", _int_bytes),
    "decimal": (b"D", _decimal_bytes),
    "float": (b"F", _float_bytes),
    "timestamp": (b"T", _timestamp_bytes),
    "text": (b"S", _text_bytes),
    "json": (b"J", _json_bytes),
}


def _encode_value(
    kind: str, *, nullable: bool, column_name: str, value: object
) -> bytes:
    """Encode one validated-schema value with a distinct type tag."""
    if value is None:
        if not nullable:
            raise ValueError(f"non-nullable column is NULL: {column_name}")
        return _frame(b"N", b"")
    tag, encode = _VALUE_ENCODERS[kind]
    return _frame(tag, encode(value))


def _decimal_pk(value: object) -> Decimal:
    _decimal_bytes(value)
    assert isinstance(value, Decimal)
    return value


def _float_pk(value: object) -> float:
    _float_bytes(value)
    assert isinstance(value, float)
    return value


_PK_ENCODERS: Final[dict[str, Callable[[object], Any]]] = {
    "bool": lambda value: _bool_bytes(value) == b"1",
    "int": lambda value: int(_int_bytes(value)),
    "decimal": _decimal_pk,
    "float": _float_pk,
    "timestamp": _timestamp,
    "text": _text_bytes,
}


def _primary_key_value(kind: str, column_name: str, value: object) -> Any:
    """Return a deterministic value suitable for tuple comparison."""
    if value is None:
        raise ValueError(f"primary-key column is NULL: {column_name}")
    return _PK_ENCODERS[kind](value)
