"""Tests for atomic GUI configuration persistence."""

from __future__ import annotations

import tomllib
from pathlib import Path
from unittest.mock import patch

import pytest

from jobfeed.config import Settings, load_settings
from jobfeed.config_editor import ConfigurationEditor, EditableConfiguration
from jobfeed.config_sources import SourcesLinkedInSearchConfig


def test_editor_preserves_private_sections_and_writes_reloadable_toml(
    tmp_path: Path,
) -> None:
    """GUI saves retain non-editable settings and complex source entries."""
    path = tmp_path / "config.toml"
    path.write_text(
        "[db]\npath = 'data/custom.sqlite'\n\n[digest]\noutput_dir = 'data/digests'\n",
        encoding="utf-8",
    )
    settings = load_settings(path)
    editable = EditableConfiguration.from_settings(settings)
    linkedin = editable.sources.linkedin.model_copy(
        update={
            "enabled": True,
            "search_urls": [
                SourcesLinkedInSearchConfig(
                    url="https://www.linkedin.com/jobs/search/?keywords=python",
                    max_jobs=12,
                    group="python",
                    group_max_jobs=5,
                )
            ],
        }
    )
    editable = editable.model_copy(
        update={"sources": editable.sources.model_copy(update={"linkedin": linkedin})}
    )

    ConfigurationEditor(path, settings, lambda _settings: None).save(editable)

    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    assert raw["db"]["path"] == "data/custom.sqlite"
    assert raw["digest"]["output_dir"] == "data/digests"
    reloaded = load_settings(path)
    entry = reloaded.sources.linkedin.search_urls[0]
    assert not isinstance(entry, str)
    assert entry.group == "python"


def test_failed_replace_preserves_original_and_does_not_apply(tmp_path: Path) -> None:
    """A filesystem failure leaves both disk and live settings untouched."""
    path = tmp_path / "config.toml"
    original = "[scoring]\nstage_a_threshold = 61\n"
    path.write_text(original, encoding="utf-8")
    settings = load_settings(path)
    editable = EditableConfiguration.from_settings(settings)
    calls: list[Settings] = []
    editor = ConfigurationEditor(path, settings, calls.append)

    with (
        patch("jobfeed.config_editor.os.replace", side_effect=OSError("disk full")),
        pytest.raises(OSError, match="disk full"),
    ):
        editor.save(editable)

    assert path.read_text(encoding="utf-8") == original
    assert calls == []
    assert list(tmp_path.glob(".config.toml.*")) == []
