"""Source setup must not require a model backend before page extraction."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from jobfeed.cli import _job_page_factory as module
from jobfeed.config import Settings
from jobfeed.domain.models import LLMRequest, Message


async def test_page_client_is_built_only_for_completion_and_reused(monkeypatch):
    client = SimpleNamespace(complete=AsyncMock(return_value="response"))
    build = MagicMock(return_value=client)
    monkeypatch.setattr(module, "build_llm_client", build)
    extractor = module.build_page_extractor(
        {"settings": Settings(), "logger": MagicMock(), "store": None}
    )
    build.assert_not_called()
    request = LLMRequest(
        model=extractor.model, messages=[Message(role="user", content="page")]
    )
    assert await extractor.client.complete(request) == "response"
    assert await extractor.client.complete(request) == "response"
    build.assert_called_once()
    client.complete.assert_awaited_with(request)
