"""Unit tests for seed_ats_companies in cli/scan.py."""

from __future__ import annotations

from jobfeed.cli.scan import seed_ats_companies
from jobfeed.domain.models import CompanyRecord

_EXISTING_FAILURES = 5
_EXISTING_JOB_COUNT = 42


class MockStore:
    """Minimal in-memory StoreOpsMixin for seed tests."""

    def __init__(self) -> None:
        self.companies: dict[str, CompanyRecord] = {}

    async def get_company(self, slug: str) -> CompanyRecord | None:
        return self.companies.get(slug)

    async def upsert_company(self, company: CompanyRecord) -> None:
        self.companies[company.slug] = company


async def test_seeds_new_slugs() -> None:
    """seed_ats_companies inserts rows for absent slugs."""
    store = MockStore()
    await seed_ats_companies(store, ["alpha", "beta"])  # type: ignore[arg-type]
    assert "alpha" in store.companies
    assert "beta" in store.companies


async def test_skips_existing_slugs() -> None:
    """seed_ats_companies does not overwrite existing company data."""
    store = MockStore()
    existing = CompanyRecord(
        slug="alpha",
        ats_vendor="greenhouse",
        consecutive_discover_failures=_EXISTING_FAILURES,
        job_count_last_scan=_EXISTING_JOB_COUNT,
    )
    store.companies["alpha"] = existing

    await seed_ats_companies(store, ["alpha"])  # type: ignore[arg-type]

    result = store.companies["alpha"]
    assert result.ats_vendor == "greenhouse"
    assert result.consecutive_discover_failures == _EXISTING_FAILURES
    assert result.job_count_last_scan == _EXISTING_JOB_COUNT


async def test_empty_slugs_is_noop() -> None:
    """seed_ats_companies with empty list does nothing."""
    store = MockStore()
    await seed_ats_companies(store, [])  # type: ignore[arg-type]
    assert len(store.companies) == 0


async def test_mixed_new_and_existing() -> None:
    """seed_ats_companies inserts only absent slugs in a mixed list."""
    store = MockStore()
    store.companies["existing"] = CompanyRecord(slug="existing", ats_vendor="ashby")

    await seed_ats_companies(store, ["existing", "newco"])  # type: ignore[arg-type]

    assert store.companies["existing"].ats_vendor == "ashby"
    assert "newco" in store.companies
    assert store.companies["newco"].ats_vendor is None
