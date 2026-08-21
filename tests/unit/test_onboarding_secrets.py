"""Contracts for local onboarding secret persistence."""

from __future__ import annotations

import stat
from pathlib import Path

from jobfeed.onboarding_secrets import ProviderSecretStore

PRIVATE_FILE_MODE = 0o600


def test_draft_api_key_is_written_with_owner_only_permissions(tmp_path: Path) -> None:
    """A locally stored onboarding key is private and can be read back."""
    path = tmp_path / "data" / "secrets.toml"
    store = ProviderSecretStore(path)

    store.save_draft("openai_api", "sk-local-test-value")

    assert stat.S_IMODE(path.stat().st_mode) == PRIVATE_FILE_MODE
    assert store.resolve("openai_api", environment={}) == "sk-local-test-value"
    assert store.has_secret("openai_api", environment={}) is True


def test_environment_key_overrides_local_draft_without_rewriting_file(
    tmp_path: Path,
) -> None:
    """Provider environment variables take precedence over stored secrets."""
    path = tmp_path / "data" / "secrets.toml"
    store = ProviderSecretStore(path)
    store.save_draft("anthropic_api", "stored-secret")
    original = path.read_bytes()

    resolved = store.resolve(
        "anthropic_api", environment={"ANTHROPIC_API_KEY": "environment-secret"}
    )

    assert resolved == "environment-secret"
    assert path.read_bytes() == original


def test_delete_draft_removes_only_the_selected_provider(tmp_path: Path) -> None:
    """Deleting one pending key preserves other provider secrets."""
    store = ProviderSecretStore(tmp_path / "data" / "secrets.toml")
    store.save_draft("openai_api", "openai-secret")
    store.save_draft("anthropic_api", "anthropic-secret")

    store.delete_draft("openai_api")

    assert store.resolve("openai_api", environment={}) is None
    assert store.resolve("anthropic_api", environment={}) == "anthropic-secret"
