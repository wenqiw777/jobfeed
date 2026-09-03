"""Stable serialization for per-run scan audit statistics."""

from __future__ import annotations

import json


def dump_scan_stats(stats: dict[str, dict[str, int]]) -> str | None:
    """Serialize non-empty scan statistics deterministically.

    Args:
        stats: Per-source nonnegative scan counters.

    Returns:
        Canonical compact JSON, or ``None`` for a legacy/non-scan run.
    """
    if not stats:
        return None
    return json.dumps(stats, sort_keys=True, separators=(",", ":"))


def load_scan_stats(value: object) -> dict[str, dict[str, int]]:
    """Hydrate validated scan statistics, preserving legacy null rows.

    Args:
        value: Database JSON text, decoded object, or legacy null.

    Returns:
        Validated per-source counters. Complexity: O(S * M), where S is the
        source count and M is the number of metrics per source.

    Raises:
        ValueError: If JSON syntax or the nested counter shape is invalid.
    """
    if value is None:
        return {}
    raw = value if isinstance(value, dict) else json.loads(str(value))
    if not isinstance(raw, dict):
        raise ValueError("scan_stats_json must contain an object")
    result: dict[str, dict[str, int]] = {}
    for source, source_stats in raw.items():
        if not isinstance(source, str) or not isinstance(source_stats, dict):
            raise ValueError("scan_stats_json has an invalid source entry")
        parsed: dict[str, int] = {}
        for metric, count in source_stats.items():
            if not isinstance(metric, str) or type(count) is not int or count < 0:
                raise ValueError("scan_stats_json has an invalid metric entry")
            parsed[metric] = count
        result[source] = parsed
    return result


__all__ = ["dump_scan_stats", "load_scan_stats"]
