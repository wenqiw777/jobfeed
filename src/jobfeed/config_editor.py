"""Safe persistence for the user-editable runtime configuration."""

from __future__ import annotations

import json
import os
import tempfile
import tomllib
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import TypeAlias

from pydantic import BaseModel, ConfigDict, Field

from jobfeed.config import (
    HardFiltersSettings,
    LLMSettings,
    MLGateSettings,
    ScoringSettings,
    Settings,
)
from jobfeed.config_sources import SourcesConfig

TomlScalar: TypeAlias = str | int | float | bool
TomlValue: TypeAlias = TomlScalar | list[object] | dict[str, object]


class EditableConfiguration(BaseModel):
    """Settings that local users may safely manage through the GUI."""

    model_config = ConfigDict(extra="forbid")

    llm: LLMSettings = Field(default_factory=LLMSettings)
    scoring: ScoringSettings = Field(default_factory=ScoringSettings)
    sources: SourcesConfig = Field(default_factory=SourcesConfig)
    hard_filters: HardFiltersSettings = Field(default_factory=HardFiltersSettings)
    ml_gate: MLGateSettings = Field(default_factory=MLGateSettings)

    @classmethod
    def from_settings(cls, settings: Settings) -> EditableConfiguration:
        """Select GUI-managed fields from complete runtime settings.

        Args:
            settings: Complete validated application settings.

        Returns:
            Validated GUI-editable projection.
        """
        return cls.model_validate(
            settings.model_dump(
                mode="json",
                include={"llm", "scoring", "sources", "hard_filters", "ml_gate"},
            )
        )

    def apply_to(self, settings: Settings) -> Settings:
        """Replace the editable sections of complete settings.

        Args:
            settings: Existing settings carrying private/runtime-only fields.

        Returns:
            New validated complete settings with this projection applied.
        """
        updates = {
            name: getattr(self, name)
            for name in ("llm", "scoring", "sources", "hard_filters", "ml_gate")
        }
        return settings.model_copy(update=updates)


class ConfigurationEditor:
    """Read and atomically replace the editable project configuration."""

    def __init__(
        self,
        path: Path,
        settings: Settings,
        apply_settings: Callable[[Settings], None],
    ) -> None:
        """Create an editor for one project-local TOML file.

        Args:
            path: Destination ``config.toml`` path.
            settings: Effective settings currently used by the process.
            apply_settings: Callback updating newly scheduled runtime work.
        """
        self._path = path.resolve()
        self._settings = settings
        self._apply_settings = apply_settings

    @property
    def is_configured(self) -> bool:
        """Return whether the user has explicitly saved configuration.

        Returns:
            True when the managed project config file exists.
        """
        return self._path.is_file()

    @property
    def editable(self) -> EditableConfiguration:
        """Return the current effective GUI-managed settings.

        Returns:
            Validated projection containing only GUI-managed fields.
        """
        return EditableConfiguration.from_settings(self._settings)

    def save(self, editable: EditableConfiguration) -> Settings:
        """Validate, atomically persist, and apply GUI settings.

        Args:
            editable: Fully validated GUI-managed configuration.

        Returns:
            Complete settings now used for newly scheduled work.

        Raises:
            OSError: If the current file cannot be read or replaced safely.
            tomllib.TOMLDecodeError: If the existing config is malformed.
        """
        updated = editable.apply_to(self._settings)
        document = self._existing_document()
        document.update(editable.model_dump(mode="json", exclude_none=True))
        _atomic_write(self._path, _render_toml(document))
        self._apply_settings(updated)
        self._settings = updated
        return updated

    def _existing_document(self) -> dict[str, object]:
        if not self._path.exists():
            return {}
        with self._path.open("rb") as stream:
            return dict(tomllib.load(stream))


def _atomic_write(path: Path, content: str) -> None:
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
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


def _render_toml(document: Mapping[str, object]) -> str:
    lines: list[str] = []
    _render_table(lines, (), document)
    return "\n".join(lines).rstrip() + "\n"


def _render_table(
    lines: list[str], path: tuple[str, ...], values: Mapping[str, object]
) -> None:
    scalar_items = [
        (key, value) for key, value in values.items() if not isinstance(value, dict)
    ]
    table_items = [
        (key, value) for key, value in values.items() if isinstance(value, dict)
    ]
    if path:
        if lines and lines[-1] != "":
            lines.append("")
        lines.append(f"[{'.'.join(path)}]")
    for key, value in scalar_items:
        if value is not None:
            lines.append(f"{key} = {_toml_value(value)}")
    for key, value in table_items:
        _render_table(lines, (*path, key), value)


def _toml_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, int | float):
        return str(value)
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    if isinstance(value, dict):
        pairs = ", ".join(f"{key} = {_toml_value(item)}" for key, item in value.items())
        return "{ " + pairs + " }"
    msg = f"unsupported TOML value: {type(value).__name__}"
    raise TypeError(msg)


__all__ = ["ConfigurationEditor", "EditableConfiguration"]
