"""LinkedIn Playwright DOM selectors and small page helpers."""

from __future__ import annotations

import random
from collections.abc import Sequence
from typing import Any

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
VIEWPORTS = (
    {"width": 1440, "height": 1000},
    {"width": 1365, "height": 900},
    {"width": 1536, "height": 960},
)
# Randomized human-like pause range between page actions. Real jitter (not a
# fixed cadence) makes an authenticated session less obviously automated.
MIN_DELAY_S = 0.8
MAX_DELAY_S = 2.4
# Authwall detection. URL hints catch the login/checkpoint REDIRECT; body hints
# are deliberately strong phrases that only appear on a real interstitial —
# generic words like "login"/"sign in" appear on valid pages and caused false
# reauth signals, so they are matched on the URL only, never on body text.
URL_AUTHWALL_HINTS = ("/login", "/checkpoint", "/authwall", "/uas/login")
BODY_AUTHWALL_HINTS = (
    "security verification",
    "verify your identity",
    "let's do a quick security check",
)


def human_delay() -> float:
    """Return a randomized human-like pause in seconds between page actions.

    Tests inject a no-op sleeper, so the value is computed but never waited on.

    Returns:
        A jittered delay between ``MIN_DELAY_S`` and ``MAX_DELAY_S``.
    """
    return random.uniform(MIN_DELAY_S, MAX_DELAY_S)


def is_browser_closed(exc: Exception) -> bool:
    """Return whether an error means the page/context/browser is gone.

    Detected by message so this confined module never imports Playwright. Lets a
    dead browser propagate instead of being masked as empty text or zero cards.

    Args:
        exc: Exception raised by a Playwright page/locator call.

    Returns:
        True when the failure is a closed target rather than a benign miss.
    """
    return "has been closed" in str(exc).lower()


CARD_SELECTOR = (
    "li[data-occludable-job-id], .job-card-container, .jobs-search-results__list-item"
)
JOB_LINK_SELECTOR = "a[href*='/jobs/view/']"
COMPANY_SELECTORS = (
    "[data-test-company-name]",
    ".job-card-container__primary-description",
    ".base-search-card__subtitle",
    ".artdeco-entity-lockup__subtitle",
)
LOCATION_SELECTORS = (
    "[data-test-job-location]",
    ".job-card-container__metadata-item",
    ".base-search-card__metadata",
    ".job-search-card__location",
)
DESCRIPTION_SELECTORS = (
    "#job-details",
    "[data-test-job-description]",
    ".jobs-description__content",
    ".description__text",
)
BODY_SELECTOR = "body"
LINKEDIN_HOME = "https://www.linkedin.com/feed/"
LINKEDIN_LOGIN = "https://www.linkedin.com/login"
DEFAULT_TIMEOUT_MS = 1500


async def read_body_text(page: Any) -> str:
    """Read page body text.

    Args:
        page: Playwright page or page-like test double.

    Returns:
        Body text, or an empty string if the DOM is not ready.
    """
    return await read_first_text(page, (BODY_SELECTOR,), timeout_ms=DEFAULT_TIMEOUT_MS)


async def read_job_description(page: Any) -> str:
    """Read the visible job description from the active LinkedIn page.

    Args:
        page: Playwright page or page-like test double.

    Returns:
        Visible JD text, or an empty string if no selector matches.
    """
    return await read_first_text(
        page,
        DESCRIPTION_SELECTORS,
        timeout_ms=DEFAULT_TIMEOUT_MS,
    )


async def read_first_text(
    owner: Any,
    selectors: Sequence[str],
    *,
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
) -> str:
    """Return the first non-empty text for a selector list.

    Args:
        owner: Playwright locator/page or compatible test double.
        selectors: Selectors to try in order.
        timeout_ms: Per-selector timeout.

    Returns:
        First non-empty normalized text, or an empty string.
    """
    for selector in selectors:
        text = await _maybe_inner_text(owner, selector, timeout_ms)
        normalized = _normalize_space(text)
        if normalized:
            return normalized
    return ""


async def read_first_attr(
    owner: Any,
    selectors: Sequence[str],
    attr: str,
    *,
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
) -> str | None:
    """Return the first present attribute for a selector list.

    Args:
        owner: Playwright locator/page or compatible test double.
        selectors: Selectors to try in order.
        attr: Attribute name to read.
        timeout_ms: Per-selector timeout.

    Returns:
        First present attribute value, or None.
    """
    for selector in selectors:
        value = await _maybe_attribute(owner, selector, attr, timeout_ms)
        if value:
            return value
    return None


def looks_like_authwall(url: str, text: str) -> bool:
    """Return whether a LinkedIn page appears to require authentication.

    Args:
        url: Current page URL.
        text: Visible page text.

    Returns:
        True when the URL/text includes LinkedIn login or checkpoint hints.
    """
    url_lower = url.lower()
    if any(hint in url_lower for hint in URL_AUTHWALL_HINTS):
        return True
    text_lower = text.lower()
    return any(hint in text_lower for hint in BODY_AUTHWALL_HINTS)


def _normalize_space(text: str) -> str:
    return " ".join(text.split())


async def _maybe_inner_text(owner: Any, selector: str, timeout_ms: int) -> str:
    try:
        value = await owner.locator(selector).first.inner_text(timeout=timeout_ms)
    except Exception as exc:
        if is_browser_closed(exc):
            raise
        return ""
    return value if isinstance(value, str) else ""


async def _maybe_attribute(
    owner: Any,
    selector: str,
    attr: str,
    timeout_ms: int,
) -> str | None:
    try:
        value = await owner.locator(selector).first.get_attribute(
            attr,
            timeout=timeout_ms,
        )
    except Exception as exc:
        if is_browser_closed(exc):
            raise
        return None
    return value if isinstance(value, str) else None


__all__ = [
    "CARD_SELECTOR",
    "COMPANY_SELECTORS",
    "DESCRIPTION_SELECTORS",
    "JOB_LINK_SELECTOR",
    "LINKEDIN_HOME",
    "LINKEDIN_LOGIN",
    "LOCATION_SELECTORS",
    "USER_AGENT",
    "VIEWPORTS",
    "human_delay",
    "is_browser_closed",
    "looks_like_authwall",
    "read_body_text",
    "read_first_attr",
    "read_first_text",
    "read_job_description",
]
