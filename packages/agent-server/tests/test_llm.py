"""Tests for resilient Gemini model selection."""

import json
from types import SimpleNamespace

import pytest

from agent_server import llm


class TemporaryModelError(Exception):
    code = 503


async def test_chat_json_uses_next_model_after_temporary_failure(
    monkeypatch: pytest.MonkeyPatch,
):
    calls: list[str] = []

    class FakeModels:
        async def generate_content(self, *, model, contents, config):
            calls.append(model)
            if model == "primary":
                raise TemporaryModelError("busy")
            return SimpleNamespace(text=json.dumps({"reply_text": "Hello"}))

    fake_client = SimpleNamespace(aio=SimpleNamespace(models=FakeModels()))
    monkeypatch.setattr(llm, "get_client", lambda: fake_client)
    monkeypatch.setenv("GEMINI_MODELS", "primary,fallback")

    result = await llm.generate_chat_json("system", [], "Hi")

    assert result == {"reply_text": "Hello"}
    assert calls == ["primary", "fallback"]
