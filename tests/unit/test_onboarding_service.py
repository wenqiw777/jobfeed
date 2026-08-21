"""Onboarding provider workflow ordering and credential contracts."""

from __future__ import annotations

import asyncio
from pathlib import Path

from jobfeed.onboarding import OnboardingProviderService
from jobfeed.onboarding_providers import ConnectionResult, ProviderModel
from jobfeed.onboarding_secrets import ProviderSecretStore
from jobfeed.onboarding_state import OnboardingDraftStore
from jobfeed.onboarding_types import ProviderName


async def test_newer_connection_request_wins_over_slower_older_request(
    tmp_path: Path,
) -> None:
    """Connection drafts follow request order, not external response timing."""
    old_started = asyncio.Event()
    release_old = asyncio.Event()

    class OutOfOrderChecker:
        async def check(
            self, provider: ProviderName, *, api_key: str | None = None
        ) -> ConnectionResult:
            assert api_key is not None
            if provider == "openai_api":
                old_started.set()
                await release_old.wait()
            return ConnectionResult(
                provider=provider,
                connected=True,
                detail=f"{provider} verified",
                models=(ProviderModel(id=f"{provider}-model", label="Model"),),
            )

    drafts = OnboardingDraftStore(tmp_path / "data" / "onboarding.json")
    service = OnboardingProviderService(
        checker=OutOfOrderChecker(),
        secrets=ProviderSecretStore(tmp_path / "data" / "secrets.toml"),
        drafts=drafts,
    )

    older = asyncio.create_task(
        service.test_connection("openai_api", api_key="openai-key")
    )
    await old_started.wait()
    newer = asyncio.create_task(
        service.test_connection("anthropic_api", api_key="anthropic-key")
    )
    await asyncio.sleep(0)
    release_old.set()
    await asyncio.gather(older, newer)

    assert drafts.load().provider == "anthropic_api"
