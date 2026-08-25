"""Map stored job platforms back to user-configured scan sources."""

from __future__ import annotations

from collections.abc import Iterable

_ATS_PLATFORMS = frozenset({"ashby", "greenhouse", "lever"})


def configured_source_counts(
    platform_counts: Iterable[tuple[str, int]],
) -> dict[str, int]:
    """Aggregate platform counts into the sources users can configure.

    Args:
        platform_counts: Stored job platform and first-insert count pairs.

    Returns:
        Counts grouped by the source choices exposed to users.
    """
    totals: dict[str, int] = {}
    for platform, count in platform_counts:
        source = "ats" if platform in _ATS_PLATFORMS else platform
        totals[source] = totals.get(source, 0) + count
    return totals


__all__ = ["configured_source_counts"]
