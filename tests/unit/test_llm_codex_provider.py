"""Host provider routing survives isolated evaluation without loading user rules."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from jobfeed.adapters.llm.codex import CodexCliLLM


def test_preserves_only_selected_openai_auth_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    (tmp_path / "config.toml").write_text(
        'model = "unrelated-model"\n'
        'model_provider = "custom"\n'
        'notify = ["unrelated-command"]\n'
        "[model_providers.custom]\n"
        'name = "OpenAI"\n'
        "requires_openai_auth = true\n"
        "supports_websockets = true\n"
        'wire_api = "responses"\n'
        'http_headers = { Authorization = "private-value" }\n'
        "[mcp_servers.unrelated]\n"
        'command = "unrelated-server"\n'
    )
    client = CodexCliLLM(model="gpt-6.1-sol", price_table={}, logger=MagicMock())
    command = client._build_command(str(tmp_path))
    assert 'model_provider="custom"' in command
    assert "model_providers.custom.requires_openai_auth=true" in command
    assert "model_providers.custom.supports_websockets=true" in command
    assert 'model_providers.custom.wire_api="responses"' in command
    assert "--ignore-user-config" in command
    assert "--ignore-rules" in command
    assert command[command.index("-m") + 1] == "gpt-6.1-sol"
    assert not any(
        excluded in argument
        for argument in command
        for excluded in ["unrelated", "private-value", "http_headers", "mcp_servers"]
    )


def test_no_provider_override_without_host_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    client = CodexCliLLM(model="gpt-6-luna", price_table={}, logger=MagicMock())
    assert not any("model_provider" in arg for arg in client._build_command("/tmp"))
