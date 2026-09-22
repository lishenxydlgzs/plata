"""Tests for resilient Gemini model selection."""

import json
import asyncio
from types import SimpleNamespace

import pytest

from agent_server import llm


class TemporaryModelError(Exception):
    code = 503


class UnavailableModelError(Exception):
    code = 404


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


async def test_chat_json_skips_unavailable_model(
    monkeypatch: pytest.MonkeyPatch,
):
    calls: list[str] = []

    class FakeModels:
        async def generate_content(self, *, model, contents, config):
            calls.append(model)
            if model == "retired":
                raise UnavailableModelError("model unavailable")
            return SimpleNamespace(text=json.dumps({"reply_text": "Hello"}))

    fake_client = SimpleNamespace(aio=SimpleNamespace(models=FakeModels()))
    monkeypatch.setattr(llm, "get_client", lambda: fake_client)
    monkeypatch.setenv("GEMINI_MODELS", "retired,available")

    result = await llm.generate_chat_json("system", [], "Hi")

    assert result == {"reply_text": "Hello"}
    assert calls == ["retired", "available"]


async def test_chat_json_bounds_each_model_attempt(
    monkeypatch: pytest.MonkeyPatch,
):
    calls: list[str] = []

    class FakeModels:
        async def generate_content(self, *, model, contents, config):
            calls.append(model)
            if model == "slow":
                await asyncio.sleep(2)
            return SimpleNamespace(text=json.dumps({"reply_text": "Hello"}))

    fake_client = SimpleNamespace(aio=SimpleNamespace(models=FakeModels()))
    monkeypatch.setattr(llm, "get_client", lambda: fake_client)
    monkeypatch.setenv("GEMINI_MODELS", "slow,fast")
    monkeypatch.setenv("GEMINI_MODEL_TIMEOUT_SECONDS", "1")

    result = await llm.generate_chat_json("system", [], "Hi")

    assert result == {"reply_text": "Hello"}
    assert calls == ["slow", "fast"]


async def test_chat_json_has_room_for_structured_learning_records(monkeypatch):
    class FakeModels:
        async def generate_content(self, *, model, contents, config):
            assert config.max_output_tokens >= 1000
            return SimpleNamespace(text='{"reply_text":"Keep going!","learning_events":[]}')

    monkeypatch.setattr(llm, "get_client", lambda: SimpleNamespace(aio=SimpleNamespace(models=FakeModels())))
    assert (await llm.generate_chat_json("system", [], "Learning report"))["reply_text"] == "Keep going!"

async def test_structured_generation_can_override_voice_deadline(monkeypatch):
    deadlines = []
    async def wait_for(awaitable, timeout):
        deadlines.append(timeout)
        return await awaitable
    class FakeModels:
        async def generate_content(self, **kwargs):
            assert kwargs['config'].max_output_tokens == 4096
            return SimpleNamespace(text='{"reply":"Hello","tool_calls":[]}')
    monkeypatch.setattr(llm, 'get_client', lambda: SimpleNamespace(aio=SimpleNamespace(models=FakeModels())))
    monkeypatch.setattr(llm.asyncio, 'wait_for', wait_for)
    monkeypatch.setenv('GEMINI_MODEL_TIMEOUT_SECONDS', '8')
    await llm.generate_chat_json('system', [], 'note', timeout_seconds=45, max_output_tokens=4096)
    assert deadlines == [45]
    assert llm.get_model_timeout_seconds() == 8
