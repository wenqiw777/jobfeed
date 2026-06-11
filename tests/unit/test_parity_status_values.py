"""Unit tests for the legacy-import status whitelist in ``parity.py``.

``parity.STATUS_VALUES`` deliberately differs from the live domain set:
legacy v16 rows still carry the four retired interview sub-statuses
(``oa``/``hr_call``/``second_round``/``final_round``), which the Alembic
0006 migration converges after import, and those rows predate the
post-legacy ``awaiting_referral`` status, so parity never validates it.
These tests pin that drift so neither set silently changes shape.
"""

from __future__ import annotations

from jobfeed.adapters.store.parity import STATUS_VALUES as PARITY_STATUS_VALUES
from jobfeed.domain.status import STATUS_VALUES as DOMAIN_STATUS_VALUES

RETIRED_STATUSES = frozenset({"oa", "hr_call", "second_round", "final_round"})

POST_LEGACY_STATUSES = frozenset({"awaiting_referral"})

EXPECTED_PARITY_STATUSES = frozenset(
    {
        # Live statuses that appear in legacy rows.
        "new",
        "scored",
        "shortlisted",
        "archived",
        "ignored",
        "applied",
        "interviewing",
        "rejected",
        "offer",
        "ghosted",
        # Retired interview sub-stages kept for legacy import validation.
        "oa",
        "hr_call",
        "second_round",
        "final_round",
    }
)


def test_parity_status_values_exact() -> None:
    """The whitelist is exactly the 14 statuses legacy rows can carry."""
    assert PARITY_STATUS_VALUES == EXPECTED_PARITY_STATUSES


def test_parity_extends_domain_with_retired_sub_stages() -> None:
    """Only the four retired sub-stages are parity-specific."""
    assert PARITY_STATUS_VALUES - DOMAIN_STATUS_VALUES == RETIRED_STATUSES


def test_domain_extends_parity_with_awaiting_referral() -> None:
    """awaiting_referral postdates legacy data, so parity omits it."""
    assert DOMAIN_STATUS_VALUES - PARITY_STATUS_VALUES == POST_LEGACY_STATUSES


def test_parity_status_values_is_frozenset() -> None:
    """The whitelist is immutable."""
    assert isinstance(PARITY_STATUS_VALUES, frozenset)
