"""Companies routes tests: CRUD over PostgreSQL + probe over a fake callable.

CRUD tests pin the Task 6 acceptance criteria against real PG: add -> list
round-trip (failure count defaults to 0, removed false), bulk upsert that
skips active slugs but restores soft-removed ones, slug normalization on
every write endpoint, soft remove hiding rows until ``include_removed``,
404 on unknown slugs, and the vendor filter. Probe tests need no database:
the probe callable is injected through the context seam, so a recording fake
store proves the endpoint performs no writes while per-entry failures stay
isolated and concurrency stays bounded.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import cast

import pytest

from jobfeed.adapters.sources._http import ProbeNetworkError
from jobfeed.cli import AppContext
from jobfeed.config import Settings
from jobfeed.web.app import build_web_app, create_web_app
from tests.web.test_app_skeleton import open_client, write_db_config

HTTP_OK = 200
HTTP_NOT_FOUND = 404
HTTP_VALIDATION_ERROR = 422

_PROBE_CONCURRENCY = 5
_CONCURRENCY_BATCH = 12
_PROBE_ENTRY_CAP = 200
_FIRST_BULK_INSERTED = 2
_SECOND_BULK_INSERTED = 1
_HOLD_S = 0.02


def _row(slug: str, vendor: str) -> dict[str, str]:
    """Build one bulk-insert request row.

    Args:
        slug: Company board slug.
        vendor: ATS vendor name.

    Returns:
        JSON-shaped bulk row.
    """
    return {"slug": slug, "vendor": vendor}


def _company(
    slug: str, vendor: str | None, *, removed: bool = False
) -> dict[str, object]:
    """Build the expected wire shape of one company row.

    Args:
        slug: Company board slug.
        vendor: ATS vendor name (None for removed rows).
        removed: Whether the row is soft-removed.

    Returns:
        Expected JSON company row with a zero failure count.
    """
    return {
        "slug": slug,
        "vendor": vendor,
        "consecutive_discover_failures": 0,
        "removed": removed,
    }


# ── CRUD (PG-backed) ─────────────────────────────────────────────────


@pytest.mark.postgres
async def test_add_then_list_roundtrip(tmp_path: Path, fresh_pg_dsn: str) -> None:
    """POST then GET returns the row with failures 0 and removed false.

    Args:
        tmp_path: Temporary root used for the config file.
        fresh_pg_dsn: DSN of a freshly migrated, empty Postgres database.
    """
    app = create_web_app(write_db_config(tmp_path, fresh_pg_dsn))

    async with open_client(app) as client:
        created = await client.post(
            "/api/companies", json={"slug": "acme", "vendor": "greenhouse"}
        )
        listed = await client.get("/api/companies")

    assert created.status_code == HTTP_OK, created.text
    assert created.json() == _company("acme", "greenhouse")
    assert listed.status_code == HTTP_OK, listed.text
    assert listed.json() == {"companies": [_company("acme", "greenhouse")]}


@pytest.mark.postgres
async def test_vendor_filter_scopes_list(tmp_path: Path, fresh_pg_dsn: str) -> None:
    """GET ?vendor= returns only that vendor's rows.

    Args:
        tmp_path: Temporary root used for the config file.
        fresh_pg_dsn: DSN of a freshly migrated, empty Postgres database.
    """
    app = create_web_app(write_db_config(tmp_path, fresh_pg_dsn))

    async with open_client(app) as client:
        await client.post(
            "/api/companies", json={"slug": "acme", "vendor": "greenhouse"}
        )
        await client.post("/api/companies", json={"slug": "beta", "vendor": "lever"})
        filtered = await client.get("/api/companies", params={"vendor": "lever"})

    assert filtered.status_code == HTTP_OK, filtered.text
    assert filtered.json() == {"companies": [_company("beta", "lever")]}


@pytest.mark.postgres
async def test_bulk_insert_then_list_roundtrip(
    tmp_path: Path, fresh_pg_dsn: str
) -> None:
    """Bulk upsert skips already-active slugs and reports the written count.

    Args:
        tmp_path: Temporary root used for the config file.
        fresh_pg_dsn: DSN of a freshly migrated, empty Postgres database.
    """
    app = create_web_app(write_db_config(tmp_path, fresh_pg_dsn))
    first_rows = [_row("acme", "greenhouse"), _row("beta", "lever")]

    async with open_client(app) as client:
        first = await client.post("/api/companies/bulk", json={"rows": first_rows})
        second = await client.post(
            "/api/companies/bulk",
            json={"rows": [*first_rows, _row("gamma", "ashby")]},
        )
        listed = await client.get("/api/companies")

    assert first.status_code == HTTP_OK, first.text
    assert first.json() == {"inserted": _FIRST_BULK_INSERTED}
    assert second.json() == {"inserted": _SECOND_BULK_INSERTED}, "active rows skip"
    assert listed.json() == {
        "companies": [
            _company("acme", "greenhouse"),
            _company("beta", "lever"),
            _company("gamma", "ashby"),
        ]
    }


@pytest.mark.postgres
async def test_bulk_restores_soft_removed_slug(
    tmp_path: Path, fresh_pg_dsn: str
) -> None:
    """A soft-removed slug in a bulk payload is restored and counted.

    Args:
        tmp_path: Temporary root used for the config file.
        fresh_pg_dsn: DSN of a freshly migrated, empty Postgres database.
    """
    app = create_web_app(write_db_config(tmp_path, fresh_pg_dsn))

    async with open_client(app) as client:
        await client.post(
            "/api/companies", json={"slug": "acme", "vendor": "greenhouse"}
        )
        await client.delete("/api/companies/acme")
        bulk = await client.post(
            "/api/companies/bulk", json={"rows": [_row("acme", "lever")]}
        )
        listed = await client.get("/api/companies")

    assert bulk.status_code == HTTP_OK, bulk.text
    assert bulk.json() == {"inserted": 1}, "restored rows count as inserted"
    assert listed.json() == {"companies": [_company("acme", "lever")]}


@pytest.mark.postgres
async def test_bulk_payload_duplicates_collapse_first_wins(
    tmp_path: Path, fresh_pg_dsn: str
) -> None:
    """Duplicate slugs within one payload collapse to the first occurrence.

    Args:
        tmp_path: Temporary root used for the config file.
        fresh_pg_dsn: DSN of a freshly migrated, empty Postgres database.
    """
    app = create_web_app(write_db_config(tmp_path, fresh_pg_dsn))
    rows = [_row("acme", "greenhouse"), _row("acme", "lever")]

    async with open_client(app) as client:
        bulk = await client.post("/api/companies/bulk", json={"rows": rows})
        listed = await client.get("/api/companies")

    assert bulk.status_code == HTTP_OK, bulk.text
    assert bulk.json() == {"inserted": 1}
    assert listed.json() == {"companies": [_company("acme", "greenhouse")]}


@pytest.mark.postgres
async def test_post_single_restores_removed_slug(
    tmp_path: Path, fresh_pg_dsn: str
) -> None:
    """POST on a soft-removed slug resurrects the row via the upsert.

    Args:
        tmp_path: Temporary root used for the config file.
        fresh_pg_dsn: DSN of a freshly migrated, empty Postgres database.
    """
    app = create_web_app(write_db_config(tmp_path, fresh_pg_dsn))

    async with open_client(app) as client:
        await client.post(
            "/api/companies", json={"slug": "acme", "vendor": "greenhouse"}
        )
        await client.delete("/api/companies/acme")
        restored = await client.post(
            "/api/companies", json={"slug": "acme", "vendor": "lever"}
        )
        listed = await client.get("/api/companies")

    assert restored.status_code == HTTP_OK, restored.text
    assert restored.json() == _company("acme", "lever")
    assert listed.json() == {"companies": [_company("acme", "lever")]}


@pytest.mark.postgres
async def test_slug_normalization_across_endpoints(
    tmp_path: Path, fresh_pg_dsn: str
) -> None:
    """POST ' Acme ' stores 'acme'; DELETE 'ACME' removes that same row.

    Args:
        tmp_path: Temporary root used for the config file.
        fresh_pg_dsn: DSN of a freshly migrated, empty Postgres database.
    """
    app = create_web_app(write_db_config(tmp_path, fresh_pg_dsn))

    async with open_client(app) as client:
        created = await client.post(
            "/api/companies", json={"slug": "  Acme ", "vendor": "greenhouse"}
        )
        listed = await client.get("/api/companies")
        deleted = await client.delete("/api/companies/ACME")
        after_delete = await client.get("/api/companies")

    assert created.status_code == HTTP_OK, created.text
    assert created.json() == _company("acme", "greenhouse")
    assert listed.json() == {"companies": [_company("acme", "greenhouse")]}
    assert deleted.status_code == HTTP_OK, deleted.text
    assert after_delete.json() == {"companies": []}


@pytest.mark.postgres
async def test_delete_hides_row_until_include_removed(
    tmp_path: Path, fresh_pg_dsn: str
) -> None:
    """DELETE soft-removes: default list hides, include_removed shows.

    Args:
        tmp_path: Temporary root used for the config file.
        fresh_pg_dsn: DSN of a freshly migrated, empty Postgres database.
    """
    app = create_web_app(write_db_config(tmp_path, fresh_pg_dsn))

    async with open_client(app) as client:
        await client.post(
            "/api/companies", json={"slug": "acme", "vendor": "greenhouse"}
        )
        deleted = await client.delete("/api/companies/acme")
        default_list = await client.get("/api/companies")
        with_removed = await client.get(
            "/api/companies", params={"include_removed": "true"}
        )

    assert deleted.status_code == HTTP_OK, deleted.text
    assert deleted.json() == {"ok": True}
    assert default_list.json() == {"companies": []}
    assert with_removed.json() == {"companies": [_company("acme", None, removed=True)]}


@pytest.mark.postgres
async def test_delete_unknown_slug_returns_404(
    tmp_path: Path, fresh_pg_dsn: str
) -> None:
    """DELETE of an untracked slug yields the shared 404 error shape.

    Args:
        tmp_path: Temporary root used for the config file.
        fresh_pg_dsn: DSN of a freshly migrated, empty Postgres database.
    """
    app = create_web_app(write_db_config(tmp_path, fresh_pg_dsn))

    async with open_client(app) as client:
        response = await client.delete("/api/companies/ghost")

    assert response.status_code == HTTP_NOT_FOUND
    error = response.json()["error"]
    assert error["code"] == "not_found"
    assert "ghost" in error["message"]
    assert error["request_id"]


# ── probe + validation (no database) ─────────────────────────────────


class _NoWriteStore:
    """JobStore stand-in: lifecycle only; any other access is recorded."""

    def __init__(self) -> None:
        """Initialize the touched-capabilities record."""
        self.touched: list[str] = []

    async def connect(self) -> None:
        """Accept the lifespan's connect call."""

    async def close(self) -> None:
        """Accept the lifespan's close call."""

    def __getattr__(self, name: str) -> object:
        """Record and reject any store capability lookup.

        Args:
            name: Looked-up attribute name.

        Raises:
            AssertionError: Always — these tests must never touch the store.
        """
        self.touched.append(name)
        raise AssertionError(f"flow must not touch the store: {name}")


class _InstrumentedProbe:
    """Fake probe tracking how many calls are in flight concurrently."""

    def __init__(self) -> None:
        """Zero the in-flight counters."""
        self.in_flight = 0
        self.max_in_flight = 0

    async def __call__(self, _slug: str) -> str | None:
        """Hold each call briefly so overlapping calls are observable.

        Args:
            _slug: Candidate slug (ignored).

        Returns:
            None — a definitive all-vendor miss for every slug.
        """
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        await asyncio.sleep(_HOLD_S)
        self.in_flight -= 1
        return None


def _probe_context(probe: object, store: _NoWriteStore | None = None) -> AppContext:
    """Build a minimal context wiring a fake probe through the context seam.

    Args:
        probe: Async callable standing in for the cli-assembled probe.
        store: Recording store; a fresh one when omitted.

    Returns:
        Context carrying the keys the web factory consumes at build time.
    """
    return cast(
        AppContext,
        {
            "store": store or _NoWriteStore(),
            "settings": Settings(),
            "probe_company": probe,
        },
    )


async def test_probe_mixed_batch_isolates_failures() -> None:
    """Hit, URL hit, definitive miss, transport error, junk: one batch."""
    probed: list[str] = []

    async def fake_probe(slug: str) -> str | None:
        probed.append(slug)
        if slug == "acme":
            return "greenhouse"
        if slug == "leverco":
            return "lever"
        if slug == "netfail":
            raise ProbeNetworkError("probe timeout", slug=slug, vendor="greenhouse")
        return None

    store = _NoWriteStore()
    app = build_web_app(_probe_context(fake_probe, store))
    entries = [
        "Acme",
        "https://jobs.lever.co/leverco/some-posting",
        "ghost",
        "netfail",
        "not a slug!!",
    ]

    async with open_client(app) as client:
        response = await client.post("/api/companies/probe", json={"entries": entries})

    assert response.status_code == HTTP_OK, response.text
    results = response.json()["results"]
    assert results[0] == {
        "input": "Acme",
        "slug": "acme",
        "vendor": "greenhouse",
        "error": None,
    }
    assert results[1] == {
        "input": "https://jobs.lever.co/leverco/some-posting",
        "slug": "leverco",
        "vendor": "lever",
        "error": None,
    }
    assert results[2] == {
        "input": "ghost",
        "slug": "ghost",
        "vendor": None,
        "error": None,
    }
    assert results[3]["slug"] == "netfail"
    assert results[3]["vendor"] is None
    assert "probe timeout" in results[3]["error"]
    assert results[4]["slug"] is None
    assert results[4]["vendor"] is None
    assert results[4]["error"]
    assert set(probed) == {"acme", "leverco", "ghost", "netfail"}, "junk never probed"
    assert store.touched == [], "probe endpoint must not touch the store"


async def test_probe_url_entries_resolve_for_all_vendors() -> None:
    """Board URLs of all three vendors reduce to their lowercase slugs."""
    probed: list[str] = []

    async def fake_probe(slug: str) -> str | None:
        probed.append(slug)
        return None

    app = build_web_app(_probe_context(fake_probe))
    entries = [
        "https://boards.greenhouse.io/Acme",
        "https://jobs.ashbyhq.com/BetaCo/8f2c-uuid",
        "https://jobs.lever.co/gamma?lever-source=email",
    ]

    async with open_client(app) as client:
        response = await client.post("/api/companies/probe", json={"entries": entries})

    assert response.status_code == HTTP_OK, response.text
    results = response.json()["results"]
    assert [result["slug"] for result in results] == ["acme", "betaco", "gamma"]
    assert all(result["error"] is None for result in results)
    assert set(probed) == {"acme", "betaco", "gamma"}


async def test_probe_concurrency_is_bounded() -> None:
    """A 12-entry batch never exceeds 5 probes in flight."""
    probe = _InstrumentedProbe()
    app = build_web_app(_probe_context(probe))
    entries = [f"slug-{index}" for index in range(_CONCURRENCY_BATCH)]

    async with open_client(app) as client:
        response = await client.post("/api/companies/probe", json={"entries": entries})

    assert response.status_code == HTTP_OK, response.text
    assert len(response.json()["results"]) == _CONCURRENCY_BATCH
    assert probe.max_in_flight == _PROBE_CONCURRENCY


async def test_probe_entry_count_bounds_return_422() -> None:
    """Empty batches and batches beyond the 200-entry cap fail validation."""
    app = build_web_app(_probe_context(_InstrumentedProbe()))
    over_cap = [f"slug-{index}" for index in range(_PROBE_ENTRY_CAP + 1)]

    async with open_client(app) as client:
        empty = await client.post("/api/companies/probe", json={"entries": []})
        too_many = await client.post("/api/companies/probe", json={"entries": over_cap})

    assert empty.status_code == HTTP_VALIDATION_ERROR
    assert empty.json()["error"]["code"] == "validation_error"
    assert too_many.status_code == HTTP_VALIDATION_ERROR
    assert too_many.json()["error"]["code"] == "validation_error"


async def test_add_invalid_vendor_returns_422_error_shape() -> None:
    """POST with an unsupported vendor fails validation before any store call."""
    store = _NoWriteStore()
    app = build_web_app(_probe_context(_InstrumentedProbe(), store))

    async with open_client(app) as client:
        response = await client.post(
            "/api/companies", json={"slug": "acme", "vendor": "workday"}
        )

    assert response.status_code == HTTP_VALIDATION_ERROR
    error = response.json()["error"]
    assert error["code"] == "validation_error"
    assert error["request_id"]
    assert store.touched == []


async def test_invalid_slug_chars_return_422_on_all_write_endpoints() -> None:
    """Slugs outside the board charset fail POST, bulk, and DELETE with 422."""
    store = _NoWriteStore()
    app = build_web_app(_probe_context(_InstrumentedProbe(), store))

    async with open_client(app) as client:
        single = await client.post(
            "/api/companies", json={"slug": "not a slug!!", "vendor": "greenhouse"}
        )
        bulk = await client.post(
            "/api/companies/bulk", json={"rows": [_row("bad!slug", "lever")]}
        )
        deleted = await client.delete("/api/companies/bad!slug")

    for response in (single, bulk, deleted):
        assert response.status_code == HTTP_VALIDATION_ERROR, response.text
        assert response.json()["error"]["code"] == "validation_error"
    assert store.touched == [], "invalid slugs never reach the store"
