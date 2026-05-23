"""Unit tests for the ATS auto-probe mechanism."""

from __future__ import annotations

import httpx
import pytest
import respx

from jobfeed.adapters.sources._ats_probe import probe_company, resolve_dead_slug
from jobfeed.adapters.sources._http import (
    ProbeIndeterminateError,
    ProbeNetworkError,
    create_http_client,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SLUG = "acme"

# Vendor probe URLs (must match what each vendor adapter hits)
GH_PROBE_URL = f"https://boards-api.greenhouse.io/v1/boards/{SLUG}"
ASHBY_PROBE_URL = (
    f"https://api.ashbyhq.com/posting-api/job-board/{SLUG}?includeCompensation=true"
)
LEVER_PROBE_URL = f"https://api.lever.co/v0/postings/{SLUG}?mode=json"

HTTP_200 = 200
HTTP_404 = 404
HTTP_410 = 410
HTTP_403 = 403
HTTP_429 = 429
HTTP_500 = 500


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_gh_hit() -> None:
    """Mock Greenhouse probe as a 200 hit."""
    respx.head(GH_PROBE_URL).mock(return_value=httpx.Response(HTTP_200))


def _mock_gh_miss(status: int = HTTP_404) -> None:
    """Mock Greenhouse probe as a definitive miss (404 or 410)."""
    respx.head(GH_PROBE_URL).mock(return_value=httpx.Response(status))


def _mock_gh_indeterminate(status: int = HTTP_500) -> None:
    """Mock Greenhouse probe as an indeterminate response."""
    respx.head(GH_PROBE_URL).mock(return_value=httpx.Response(status))


def _mock_gh_network_error() -> None:
    """Mock Greenhouse probe as a network-level error."""
    respx.head(GH_PROBE_URL).mock(side_effect=httpx.TimeoutException("gh timeout"))


def _mock_ashby_hit() -> None:
    """Mock Ashby probe as a 200 hit (Ashby uses GET on jobs URL)."""
    respx.get(ASHBY_PROBE_URL).mock(return_value=httpx.Response(HTTP_200))


def _mock_ashby_miss(status: int = HTTP_404) -> None:
    """Mock Ashby probe as a definitive miss."""
    respx.get(ASHBY_PROBE_URL).mock(return_value=httpx.Response(status))


def _mock_ashby_indeterminate(status: int = HTTP_429) -> None:
    """Mock Ashby probe as an indeterminate response."""
    respx.get(ASHBY_PROBE_URL).mock(return_value=httpx.Response(status))


def _mock_ashby_network_error() -> None:
    """Mock Ashby probe as a network-level error."""
    respx.get(ASHBY_PROBE_URL).mock(side_effect=httpx.TimeoutException("ashby timeout"))


def _mock_lever_hit() -> None:
    """Mock Lever probe as a 200 hit with valid JSON list."""
    respx.get(LEVER_PROBE_URL).mock(return_value=httpx.Response(HTTP_200, json=[]))


def _mock_lever_miss(status: int = HTTP_404) -> None:
    """Mock Lever probe as a definitive miss."""
    respx.get(LEVER_PROBE_URL).mock(return_value=httpx.Response(status))


def _mock_lever_indeterminate(status: int = HTTP_500) -> None:
    """Mock Lever probe as an indeterminate response."""
    respx.get(LEVER_PROBE_URL).mock(return_value=httpx.Response(status))


def _mock_lever_network_error() -> None:
    """Mock Lever probe as a network-level error."""
    respx.get(LEVER_PROBE_URL).mock(side_effect=httpx.TimeoutException("lever timeout"))


# ===========================================================================
# probe_company — vendor hit scenarios
# ===========================================================================


class TestProbeCompanyHits:
    """Tests for probe_company returning a vendor name on a hit."""

    @respx.mock
    async def test_returns_greenhouse_when_greenhouse_hits(self) -> None:
        """Returns 'greenhouse' when Greenhouse probe succeeds."""
        _mock_gh_hit()
        async with create_http_client() as client:
            result = await probe_company(client, SLUG)
        assert result == "greenhouse"

    @respx.mock
    async def test_returns_ashby_when_only_ashby_hits(self) -> None:
        """Returns 'ashby' when Greenhouse misses but Ashby probe succeeds."""
        _mock_gh_miss()
        _mock_ashby_hit()
        async with create_http_client() as client:
            result = await probe_company(client, SLUG)
        assert result == "ashby"

    @respx.mock
    async def test_returns_lever_when_only_lever_hits(self) -> None:
        """Returns 'lever' when Greenhouse and Ashby miss but Lever hits."""
        _mock_gh_miss()
        _mock_ashby_miss()
        _mock_lever_hit()
        async with create_http_client() as client:
            result = await probe_company(client, SLUG)
        assert result == "lever"

    @respx.mock
    async def test_returns_greenhouse_first_regardless_of_others(self) -> None:
        """Returns 'greenhouse' without probing others when Greenhouse hits first."""
        _mock_gh_hit()
        # Ashby and Lever routes are not registered; hitting them would error.
        async with create_http_client() as client:
            result = await probe_company(client, SLUG)
        assert result == "greenhouse"

    @respx.mock
    async def test_greenhouse_network_error_tolerated_when_ashby_hits(self) -> None:
        """Partial network failure on Greenhouse is tolerated when Ashby hits."""
        _mock_gh_network_error()
        _mock_ashby_hit()
        async with create_http_client() as client:
            result = await probe_company(client, SLUG)
        assert result == "ashby"

    @respx.mock
    async def test_greenhouse_indeterminate_tolerated_when_ashby_hits(self) -> None:
        """Indeterminate Greenhouse response is tolerated when Ashby hits."""
        _mock_gh_indeterminate()
        _mock_ashby_hit()
        async with create_http_client() as client:
            result = await probe_company(client, SLUG)
        assert result == "ashby"

    @respx.mock
    async def test_greenhouse_and_ashby_network_error_tolerated_when_lever_hits(
        self,
    ) -> None:
        """Two network failures are tolerated when Lever still hits."""
        _mock_gh_network_error()
        _mock_ashby_network_error()
        _mock_lever_hit()
        async with create_http_client() as client:
            result = await probe_company(client, SLUG)
        assert result == "lever"

    @respx.mock
    async def test_greenhouse_indeterminate_and_ashby_miss_lever_hits(self) -> None:
        """Indeterminate Greenhouse and Ashby miss are tolerated when Lever hits."""
        _mock_gh_indeterminate(HTTP_403)
        _mock_ashby_miss()
        _mock_lever_hit()
        async with create_http_client() as client:
            result = await probe_company(client, SLUG)
        assert result == "lever"

    @respx.mock
    async def test_greenhouse_miss_ashby_network_error_lever_hits(self) -> None:
        """Definitive Greenhouse miss and Ashby network error tolerated; Lever hits."""
        _mock_gh_miss()
        _mock_ashby_network_error()
        _mock_lever_hit()
        async with create_http_client() as client:
            result = await probe_company(client, SLUG)
        assert result == "lever"


# ===========================================================================
# probe_company — definitive miss (all vendors 404/410)
# ===========================================================================


class TestProbeCompanyAllMisses:
    """Tests for probe_company returning None when all vendors miss definitively."""

    @respx.mock
    async def test_returns_none_when_all_vendors_return_404(self) -> None:
        """Returns None when all three vendors return 404."""
        _mock_gh_miss(HTTP_404)
        _mock_ashby_miss(HTTP_404)
        _mock_lever_miss(HTTP_404)
        async with create_http_client() as client:
            result = await probe_company(client, SLUG)
        assert result is None

    @respx.mock
    async def test_returns_none_when_all_vendors_return_410(self) -> None:
        """Returns None when all three vendors return 410."""
        _mock_gh_miss(HTTP_410)
        _mock_ashby_miss(HTTP_410)
        _mock_lever_miss(HTTP_410)
        async with create_http_client() as client:
            result = await probe_company(client, SLUG)
        assert result is None

    @respx.mock
    async def test_returns_none_when_vendors_return_mixed_404_and_410(self) -> None:
        """Returns None when vendors return 404 and 410 in any combination."""
        _mock_gh_miss(HTTP_404)
        _mock_ashby_miss(HTTP_410)
        _mock_lever_miss(HTTP_404)
        async with create_http_client() as client:
            result = await probe_company(client, SLUG)
        assert result is None


# ===========================================================================
# probe_company — ProbeNetworkError
# ===========================================================================


class TestProbeCompanyNetworkErrors:
    """Tests for probe_company raising ProbeNetworkError."""

    @respx.mock
    async def test_raises_probe_network_error_when_all_probes_fail_at_network_level(
        self,
    ) -> None:
        """Raises ProbeNetworkError when all three vendor probes time out."""
        _mock_gh_network_error()
        _mock_ashby_network_error()
        _mock_lever_network_error()
        async with create_http_client() as client:
            with pytest.raises(ProbeNetworkError):
                await probe_company(client, SLUG)

    @respx.mock
    async def test_raises_probe_network_error_with_mixed_network_and_miss(
        self,
    ) -> None:
        """Raises ProbeNetworkError when some miss and others have network errors."""
        _mock_gh_network_error()
        _mock_ashby_network_error()
        _mock_lever_miss(HTTP_404)
        async with create_http_client() as client:
            with pytest.raises(ProbeNetworkError):
                await probe_company(client, SLUG)

    @respx.mock
    async def test_raises_probe_network_error_when_single_network_error_no_hits(
        self,
    ) -> None:
        """Raises ProbeNetworkError when only one probe errors and others miss."""
        _mock_gh_miss(HTTP_404)
        _mock_ashby_miss(HTTP_404)
        _mock_lever_network_error()
        async with create_http_client() as client:
            with pytest.raises(ProbeNetworkError):
                await probe_company(client, SLUG)


# ===========================================================================
# probe_company — ProbeIndeterminateError
# ===========================================================================


class TestProbeCompanyIndeterminateErrors:
    """Tests for probe_company raising ProbeIndeterminateError."""

    @respx.mock
    async def test_raises_indeterminate_when_no_hit_and_any_indeterminate_response(
        self,
    ) -> None:
        """Raises ProbeIndeterminateError when no vendor hits and any is ambiguous."""
        _mock_gh_miss(HTTP_404)
        _mock_ashby_indeterminate(HTTP_429)
        _mock_lever_miss(HTTP_404)
        async with create_http_client() as client:
            with pytest.raises(ProbeIndeterminateError):
                await probe_company(client, SLUG)

    @respx.mock
    async def test_raises_indeterminate_on_403_response_with_no_hits(self) -> None:
        """Raises ProbeIndeterminateError when Greenhouse returns 403; no other hits."""
        _mock_gh_indeterminate(HTTP_403)
        _mock_ashby_miss(HTTP_404)
        _mock_lever_miss(HTTP_404)
        async with create_http_client() as client:
            with pytest.raises(ProbeIndeterminateError):
                await probe_company(client, SLUG)

    @respx.mock
    async def test_raises_indeterminate_on_500_with_no_hits(self) -> None:
        """Raises ProbeIndeterminateError when Lever returns 500 and no others hit."""
        _mock_gh_miss(HTTP_404)
        _mock_ashby_miss(HTTP_404)
        _mock_lever_indeterminate(HTTP_500)
        async with create_http_client() as client:
            with pytest.raises(ProbeIndeterminateError):
                await probe_company(client, SLUG)

    @respx.mock
    async def test_raises_indeterminate_takes_priority_over_network_error(
        self,
    ) -> None:
        """ProbeIndeterminateError takes priority when both types of failure occur."""
        _mock_gh_network_error()
        _mock_ashby_indeterminate(HTTP_429)
        _mock_lever_miss(HTTP_404)
        async with create_http_client() as client:
            with pytest.raises(ProbeIndeterminateError):
                await probe_company(client, SLUG)

    @respx.mock
    async def test_raises_indeterminate_on_lever_invalid_json_with_no_other_hits(
        self,
    ) -> None:
        """Raises ProbeIndeterminateError when Lever 200 returns non-list JSON body."""
        _mock_gh_miss(HTTP_404)
        _mock_ashby_miss(HTTP_404)
        # Lever expects a list; a dict triggers ProbeIndeterminateError in lever.probe.
        respx.get(LEVER_PROBE_URL).mock(
            return_value=httpx.Response(HTTP_200, json={"jobs": []})
        )
        async with create_http_client() as client:
            with pytest.raises(ProbeIndeterminateError):
                await probe_company(client, SLUG)

    @respx.mock
    async def test_raises_indeterminate_all_vendors_indeterminate(self) -> None:
        """Raises ProbeIndeterminateError when all three vendors are indeterminate."""
        _mock_gh_indeterminate(HTTP_500)
        _mock_ashby_indeterminate(HTTP_429)
        _mock_lever_indeterminate(HTTP_500)
        async with create_http_client() as client:
            with pytest.raises(ProbeIndeterminateError):
                await probe_company(client, SLUG)


# ===========================================================================
# resolve_dead_slug — identical semantics to probe_company
# ===========================================================================


class TestResolveDeadSlug:
    """Tests for resolve_dead_slug re-probing all vendors including cached vendor."""

    @respx.mock
    async def test_returns_same_vendor_when_cached_vendor_reprobe_succeeds(
        self,
    ) -> None:
        """Returns 'greenhouse' when cached Greenhouse vendor recovers on re-probe."""
        _mock_gh_hit()
        async with create_http_client() as client:
            result = await resolve_dead_slug(client, SLUG)
        assert result == "greenhouse"

    @respx.mock
    async def test_returns_new_vendor_on_ats_migration(self) -> None:
        """Returns 'ashby' when company migrated from Greenhouse to Ashby."""
        _mock_gh_miss(HTTP_404)
        _mock_ashby_hit()
        async with create_http_client() as client:
            result = await resolve_dead_slug(client, SLUG)
        assert result == "ashby"

    @respx.mock
    async def test_returns_lever_on_migration_from_greenhouse(self) -> None:
        """Returns 'lever' when company migrated from Greenhouse to Lever."""
        _mock_gh_miss(HTTP_404)
        _mock_ashby_miss(HTTP_404)
        _mock_lever_hit()
        async with create_http_client() as client:
            result = await resolve_dead_slug(client, SLUG)
        assert result == "lever"

    @respx.mock
    async def test_returns_none_when_all_vendors_return_definitive_miss(self) -> None:
        """Returns None when slug is truly dead (all vendors return 404/410)."""
        _mock_gh_miss(HTTP_404)
        _mock_ashby_miss(HTTP_410)
        _mock_lever_miss(HTTP_404)
        async with create_http_client() as client:
            result = await resolve_dead_slug(client, SLUG)
        assert result is None

    @respx.mock
    async def test_raises_probe_network_error_when_all_probes_fail_network_level(
        self,
    ) -> None:
        """Raises ProbeNetworkError when all vendor probes fail at the network level."""
        _mock_gh_network_error()
        _mock_ashby_network_error()
        _mock_lever_network_error()
        async with create_http_client() as client:
            with pytest.raises(ProbeNetworkError):
                await resolve_dead_slug(client, SLUG)

    @respx.mock
    async def test_raises_probe_indeterminate_when_any_probe_is_ambiguous(
        self,
    ) -> None:
        """Raises ProbeIndeterminateError when no vendor hits and one is ambiguous."""
        _mock_gh_miss(HTTP_404)
        _mock_ashby_indeterminate(HTTP_429)
        _mock_lever_miss(HTTP_404)
        async with create_http_client() as client:
            with pytest.raises(ProbeIndeterminateError):
                await resolve_dead_slug(client, SLUG)

    @respx.mock
    async def test_reprobe_includes_originally_cached_vendor(self) -> None:
        """Confirms resolve_dead_slug re-probes Greenhouse (cached vendor scenario)."""
        # If cached vendor was lever but now greenhouse hits, return greenhouse.
        _mock_gh_hit()
        async with create_http_client() as client:
            result = await resolve_dead_slug(client, SLUG)
        # Greenhouse is probed first by market share order.
        assert result == "greenhouse"

    @respx.mock
    async def test_network_error_and_miss_raises_network_error(self) -> None:
        """Raises ProbeNetworkError when network fails for some; others return miss."""
        _mock_gh_network_error()
        _mock_ashby_miss(HTTP_404)
        _mock_lever_miss(HTTP_404)
        async with create_http_client() as client:
            with pytest.raises(ProbeNetworkError):
                await resolve_dead_slug(client, SLUG)
