"""Private TOML and environment merge helpers for runtime configuration."""

from __future__ import annotations

import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import cast

CONFIG_ENV_PREFIX = "JOBFEED_"
ENV_NESTED_DELIMITER = "__"


def load_toml_file(config_path: Path | None) -> dict[str, object]:
    """Load one optional TOML document without applying defaults.

    Args:
        config_path: Explicit TOML path, or None for an empty document.

    Returns:
        Parsed configuration mapping.

    Raises:
        FileNotFoundError: If the explicit path does not exist.
    """
    if config_path is None:
        return {}
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")
    with config_path.open("rb") as config_file:
        return cast(dict[str, object], tomllib.load(config_file))


def collect_env_overrides(environ: Mapping[str, str]) -> dict[str, object]:
    """Collect explicit nested ``JOBFEED_*`` environment overrides.

    Args:
        environ: Environment mapping to inspect.

    Returns:
        Nested configuration mapping for recognized double-underscore keys.
    """
    overrides: dict[str, object] = {}
    for key, value in environ.items():
        if not key.startswith(CONFIG_ENV_PREFIX):
            continue
        raw_path = key.removeprefix(CONFIG_ENV_PREFIX)
        if ENV_NESTED_DELIMITER not in raw_path:
            continue
        path = raw_path.lower().split(ENV_NESTED_DELIMITER)
        if not all(path):
            continue
        _set_nested_value(overrides, path, value)
    return overrides


def apply_database_env_aliases(
    environ: Mapping[str, str],
    overrides: dict[str, object],
) -> None:
    """Map flat database aliases while preserving nested-key precedence.

    Args:
        environ: Environment mapping to inspect.
        overrides: Mutable nested overrides receiving recognized aliases.
    """
    _apply_flat_alias(environ, overrides, "JOBFEED_DB_PATH", "db_path", "path")
    _apply_flat_alias(environ, overrides, "JOBFEED_DB_URL", "db_url", "url")


def merge_dicts(
    base: dict[str, object],
    overrides: dict[str, object],
) -> dict[str, object]:
    """Recursively merge overrides without mutating either input mapping.

    Args:
        base: Lower-precedence configuration mapping.
        overrides: Higher-precedence configuration mapping.

    Returns:
        Fresh recursively merged configuration mapping.
    """
    merged = dict(base)
    for key, override_value in overrides.items():
        base_value = merged.get(key)
        if isinstance(base_value, dict) and isinstance(override_value, dict):
            merged[key] = merge_dicts(
                cast(dict[str, object], base_value),
                cast(dict[str, object], override_value),
            )
            continue
        merged[key] = override_value
    return merged


def _apply_flat_alias(
    environ: Mapping[str, str],
    overrides: dict[str, object],
    env_name: str,
    flat_key: str,
    field_name: str,
) -> None:
    value = environ.get(env_name)
    if value is None:
        return
    overrides.pop(flat_key, None)
    db_section = overrides.setdefault("db", {})
    if isinstance(db_section, dict):
        db_section.setdefault(field_name, value)


def _set_nested_value(target: dict[str, object], path: list[str], value: str) -> None:
    current = target
    for part in path[:-1]:
        next_value = current.get(part)
        if not isinstance(next_value, dict):
            next_value = {}
            current[part] = next_value
        current = cast(dict[str, object], next_value)
    current[path[-1]] = value


__all__ = [
    "apply_database_env_aliases",
    "collect_env_overrides",
    "load_toml_file",
    "merge_dicts",
]
