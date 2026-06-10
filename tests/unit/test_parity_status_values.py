"""Unit tests for parity.py STATUS_VALUES (Phase 6).

The parity module needs to validate legacy import data that may contain the
four retired interview sub-statuses. This test file pins that behavior.

Note: parity.STATUS_VALUES deliberately omits 'awaiting_referral' because
legacy import data predates Phase 6 and never contains that status. The
parity set only needs to handle statuses that actually appear in legacy rows.
"""

from __future__ import annotations

from jobfeed.adapters.store.parity import STATUS_VALUES as PARITY_STATUS_VALUES
from jobfeed.domain.status import STATUS_VALUES as DOMAIN_STATUS_VALUES


# ---- Constants ----

_RETIRED_STATUSES = frozenset({"oa", "hr_call", "second_round", "final_round"})

# Core statuses present in both domain and parity (excludes awaiting_referral
# which is Phase 6-new and won't appear in legacy rows).
_COMMON_STATUSES = DOMAIN_STATUS_VALUES - {"awaiting_referral"}


class TestParityStatusValuesRetiredValues:
    """Tests that parity STATUS_VALUES includes the four retired statuses."""

    def test_parity_includes_retired_oa(self) -> None:
        """'oa' must be in parity STATUS_VALUES for legacy import rows."""
        assert "oa" in PARITY_STATUS_VALUES

    def test_parity_includes_retired_hr_call(self) -> None:
        """'hr_call' must be in parity STATUS_VALUES for legacy import rows."""
        assert "hr_call" in PARITY_STATUS_VALUES

    def test_parity_includes_retired_second_round(self) -> None:
        """'second_round' must be in parity STATUS_VALUES for legacy import rows."""
        assert "second_round" in PARITY_STATUS_VALUES

    def test_parity_includes_retired_final_round(self) -> None:
        """'final_round' must be in parity STATUS_VALUES for legacy import rows."""
        assert "final_round" in PARITY_STATUS_VALUES

    def test_all_four_retired_statuses_present(self) -> None:
        """All four retired Phase 5 statuses must be in parity STATUS_VALUES."""
        assert _RETIRED_STATUSES.issubset(PARITY_STATUS_VALUES)


class TestParityStatusValuesLiveValues:
    """Tests that parity STATUS_VALUES includes the core live domain statuses."""

    def test_parity_includes_new(self) -> None:
        """'new' must be in parity STATUS_VALUES."""
        assert "new" in PARITY_STATUS_VALUES

    def test_parity_includes_scored(self) -> None:
        """'scored' must be in parity STATUS_VALUES."""
        assert "scored" in PARITY_STATUS_VALUES

    def test_parity_includes_applied(self) -> None:
        """'applied' must be in parity STATUS_VALUES."""
        assert "applied" in PARITY_STATUS_VALUES

    def test_parity_includes_interviewing(self) -> None:
        """'interviewing' must be in parity STATUS_VALUES."""
        assert "interviewing" in PARITY_STATUS_VALUES

    def test_parity_includes_offer(self) -> None:
        """'offer' must be in parity STATUS_VALUES."""
        assert "offer" in PARITY_STATUS_VALUES

    def test_parity_includes_rejected(self) -> None:
        """'rejected' must be in parity STATUS_VALUES."""
        assert "rejected" in PARITY_STATUS_VALUES

    def test_parity_includes_ghosted(self) -> None:
        """'ghosted' must be in parity STATUS_VALUES."""
        assert "ghosted" in PARITY_STATUS_VALUES

    def test_parity_includes_archived(self) -> None:
        """'archived' must be in parity STATUS_VALUES."""
        assert "archived" in PARITY_STATUS_VALUES

    def test_parity_includes_ignored(self) -> None:
        """'ignored' must be in parity STATUS_VALUES."""
        assert "ignored" in PARITY_STATUS_VALUES

    def test_parity_includes_shortlisted(self) -> None:
        """'shortlisted' must be in parity STATUS_VALUES."""
        assert "shortlisted" in PARITY_STATUS_VALUES

    def test_parity_includes_common_statuses(self) -> None:
        """All non-awaiting_referral domain statuses should be in parity."""
        assert _COMMON_STATUSES.issubset(PARITY_STATUS_VALUES)


class TestParityStatusValuesStructure:
    """Tests for the structural properties of parity STATUS_VALUES."""

    def test_parity_status_values_is_frozenset(self) -> None:
        """STATUS_VALUES in parity should be a frozenset."""
        assert isinstance(PARITY_STATUS_VALUES, frozenset)

    def test_parity_has_14_values(self) -> None:
        """parity STATUS_VALUES should have exactly 14 values.

        10 live domain values (excluding awaiting_referral) + 4 retired = 14.
        """
        assert len(PARITY_STATUS_VALUES) == 14

    def test_awaiting_referral_not_in_parity(self) -> None:
        """awaiting_referral is Phase 6-new and should NOT appear in parity.

        Legacy import data predates this status, so parity never needs to
        validate it.
        """
        assert "awaiting_referral" not in PARITY_STATUS_VALUES

    def test_retired_statuses_not_in_domain(self) -> None:
        """The four retired statuses should NOT be in the live domain STATUS_VALUES."""
        assert not _RETIRED_STATUSES.intersection(DOMAIN_STATUS_VALUES)


class TestParityStatusValuesVsDomain:
    """Comparison tests between parity and domain STATUS_VALUES."""

    def test_parity_contains_retired_not_in_domain(self) -> None:
        """parity should have 4 values not in domain (the retired sub-statuses)."""
        extra_in_parity = PARITY_STATUS_VALUES - DOMAIN_STATUS_VALUES
        assert extra_in_parity == _RETIRED_STATUSES

    def test_domain_has_awaiting_referral_not_in_parity(self) -> None:
        """domain should have 'awaiting_referral' which parity doesn't need."""
        extra_in_domain = DOMAIN_STATUS_VALUES - PARITY_STATUS_VALUES
        assert extra_in_domain == {"awaiting_referral"}

    def test_domain_has_awaiting_referral(self) -> None:
        """Domain STATUS_VALUES should have awaiting_referral (Phase 6 new status)."""
        assert "awaiting_referral" in DOMAIN_STATUS_VALUES
