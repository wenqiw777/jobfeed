"""Company/title normalization re-exports for store adapters.

The rules live in the pure domain layer (`jobfeed.domain.normalize`); this
module re-exports them so `postgres.py`'s import site and behavior stay stable.
"""

from __future__ import annotations

from jobfeed.domain.normalize import normalize, normalize_company

__all__ = [
    "normalize",
    "normalize_company",
]
