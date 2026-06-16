"""Unit tests for the pure pasted-entry to ATS-slug normalizer."""

from __future__ import annotations

import pytest

from jobfeed.domain.company_slug import (
    is_valid_slug,
    normalize_company_entry,
    normalize_slug,
)


@pytest.mark.parametrize(
    ("entry", "expected_slug"),
    [
        ("acme", "acme"),
        ("  Acme ", "acme"),
        ("data-dog_2", "data-dog_2"),
        ("https://boards.greenhouse.io/Acme", "acme"),
        ("https://boards.eu.greenhouse.io/acme", "acme"),
        ("https://job-boards.greenhouse.io/acme/jobs/123", "acme"),
        ("boards.greenhouse.io/acme", "acme"),
        ("https://jobs.ashbyhq.com/BetaCo/8f2c-uuid", "betaco"),
        ("https://jobs.lever.co/gamma?lever-source=email", "gamma"),
        ("https://boards.greenhouse.io/embed/job_app?for=acme&token=1", "acme"),
        ("HTTPS://BOARDS.GREENHOUSE.IO/Acme", "acme"),
        ("https://boards.greenhouse.io/embed/job_app?FOR=Acme&token=1", "acme"),
    ],
)
def test_entries_resolve_to_slugs(entry: str, expected_slug: str) -> None:
    """Plain slugs and vendor board URLs normalize to a lowercase slug.

    Args:
        entry: Raw pasted entry.
        expected_slug: Slug the entry should normalize to.
    """
    result = normalize_company_entry(entry)
    assert result.error is None
    assert result.slug == expected_slug


@pytest.mark.parametrize(
    "entry",
    [
        "",
        "   ",
        "not a slug!!",
        "-leading-dash",
        "https://example.com/acme",
        "boards.greenhouse.io/",
        "https://boards.greenhouse.io/embed/job_app",
    ],
)
def test_junk_entries_yield_errors(entry: str) -> None:
    """Blank, malformed, and unknown-host entries report an error, no slug.

    Args:
        entry: Raw pasted entry expected to fail normalization.
    """
    result = normalize_company_entry(entry)
    assert result.slug is None
    assert result.error


def test_normalize_slug_trims_and_lowercases() -> None:
    """The shared slug normalizer trims whitespace and lowercases."""
    assert normalize_slug("  Acme ") == "acme"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("acme", True),
        ("data-dog_2", True),
        ("", False),
        ("-leading-dash", False),
        ("not a slug!!", False),
        ("Acme", False),  # validity is checked post-normalization
    ],
)
def test_is_valid_slug(value: str, expected: bool) -> None:
    """Only normalized text in the vendor-board slug charset is valid.

    Args:
        value: Candidate slug text.
        expected: Whether the text should be accepted.
    """
    assert is_valid_slug(value) is expected
