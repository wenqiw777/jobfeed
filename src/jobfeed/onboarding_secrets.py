"""Private local persistence for API keys entered during onboarding."""

from __future__ import annotations

import json
import os
import tempfile
import tomllib
from collections.abc import Mapping
from pathlib import Path

from jobfeed.onboarding_types import ProviderName

_ENV_NAMES = {
    "openai_api": "OPENAI_API_KEY",
    "anthropic_api": "ANTHROPIC_API_KEY",
}
_KEY_NAMES = {
    "openai_api": "openai_api_key",
    "anthropic_api": "anthropic_api_key",
}


class ProviderSecretStore:
    """Read and atomically update provider secrets without exposing values."""

    def __init__(self, path: Path) -> None:
        """Create a store for the project-local ``data/secrets.toml`` file."""
        self._path = path.resolve()

    def save_draft(self, provider: ProviderName, value: str) -> None:
        """Persist a non-empty pending API key for final confirmation.

        Args:
            provider: Official API provider owning the key.
            value: Non-empty raw API key.

        Raises:
            ValueError: If the provider is a CLI or the value is empty.
        """
        key = _secret_key(provider)
        if not value:
            raise ValueError("API key must not be empty")
        document = self._read()
        draft = _table(document, "draft")
        draft[key] = value
        document["draft"] = draft
        _write_private(self._path, _render(document))

    def delete_draft(self, provider: ProviderName) -> None:
        """Remove only the pending key for one API provider.

        Args:
            provider: Official API provider whose pending key is removed.
        """
        key = _secret_key(provider)
        document = self._read()
        draft = _table(document, "draft")
        draft.pop(key, None)
        document["draft"] = draft
        _write_private(self._path, _render(document))

    def resolve(
        self,
        provider: ProviderName,
        *,
        environment: Mapping[str, str] | None = None,
    ) -> str | None:
        """Resolve environment, pending, then active secret precedence.

        Args:
            provider: Official API provider whose key is needed.
            environment: Optional environment mapping, primarily for tests.

        Returns:
            Resolved raw key for an outbound provider call, or None.
        """
        key = _secret_key(provider)
        env = os.environ if environment is None else environment
        environment_value = env.get(_ENV_NAMES[provider])
        if environment_value:
            return environment_value
        document = self._read()
        for table_name in ("draft", "active"):
            value = _table(document, table_name).get(key)
            if isinstance(value, str) and value:
                return value
        return None

    def has_secret(
        self,
        provider: ProviderName,
        *,
        environment: Mapping[str, str] | None = None,
    ) -> bool:
        """Return connection-key presence without returning its value.

        Args:
            provider: Provider whose key presence is checked.
            environment: Optional environment mapping, primarily for tests.

        Returns:
            True when an environment or local key is available.
        """
        if provider not in _ENV_NAMES:
            return False
        return self.resolve(provider, environment=environment) is not None

    def _read(self) -> dict[str, object]:
        if not self._path.exists():
            return {}
        with self._path.open("rb") as stream:
            return dict(tomllib.load(stream))


def _secret_key(provider: ProviderName) -> str:
    try:
        return _KEY_NAMES[provider]
    except KeyError as exc:
        raise ValueError(f"provider does not use an API key: {provider}") from exc


def _table(document: Mapping[str, object], name: str) -> dict[str, object]:
    value = document.get(name, {})
    return dict(value) if isinstance(value, dict) else {}


def _render(document: Mapping[str, object]) -> str:
    """Render two small secret tables. Time complexity: O(stored keys)."""
    lines: list[str] = []
    for table_name in ("active", "draft"):
        values = _table(document, table_name)
        if not values:
            continue
        if lines:
            lines.append("")
        lines.append(f"[{table_name}]")
        for key, value in sorted(values.items()):
            if isinstance(value, str):
                lines.append(f"{key} = {json.dumps(value)}")
    return "\n".join(lines).rstrip() + "\n"


def _write_private(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    finally:
        temporary.unlink(missing_ok=True)


__all__ = ["ProviderSecretStore"]
