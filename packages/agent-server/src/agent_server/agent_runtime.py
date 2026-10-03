"""Shared Strands runtime with the application's Gemini fallback policy."""

import asyncio
import logging
import json

from strands import Agent
from strands.hooks import AfterToolCallEvent, BeforeModelCallEvent, HookProvider, HookRegistry
from strands.models.gemini import GeminiModel
from strands.tools.executors import SequentialToolExecutor

from .llm import get_client, get_models, get_model_timeout_seconds, _is_fallback_error

logger = logging.getLogger(__name__)


class FallbackGeminiModel(GeminiModel):
    """Retry model calls before visible output, never replay an agent/tool run."""

    def __init__(self, *, timeout_seconds=None, max_output_tokens=4096, buffered=False, json_output=False, temperature=0.7):
        self.models = get_models()
        self.buffered = buffered
        self.timeout_seconds = timeout_seconds or get_model_timeout_seconds()
        super().__init__(client=get_client(), model_id=self.models[0],
                         params={"max_output_tokens": max_output_tokens, "temperature": temperature,
                                 **({"response_mime_type": "application/json"} if json_output else {})})

    async def stream(self, *args, **kwargs):
        for index, model_id in enumerate(self.models):
            self.update_config(model_id=model_id)
            started = False
            prelude = []
            try:
                async with asyncio.timeout(self.timeout_seconds):
                    async for event in super().stream(*args, **kwargs):
                        # Buffer framing events until actual content starts. A failed
                        # first attempt must not leave a half-open message on the wire.
                        if not started:
                            prelude.append(event)
                            if self.buffered:
                                continue
                            if "contentBlockDelta" not in event and "contentBlockStart" not in event:
                                continue
                            started = True
                            for item in prelude:
                                yield item
                            prelude.clear()
                        else:
                            yield event
                    for item in prelude:
                        yield item
                return
            except Exception as error:
                if started or index == len(self.models) - 1 or not (
                    isinstance(error, TimeoutError) or _is_fallback_error(error)
                    or (error.__cause__ is not None and _is_fallback_error(error.__cause__))
                ):
                    raise
                logger.warning("Gemini model %s unavailable; trying configured fallback", model_id)


class StagedToolGuard(HookProvider):
    """Catch SDK argument-validation failures that happen before a tool body runs."""

    def __init__(self):
        self.failed = False

    def register_hooks(self, registry: HookRegistry):
        registry.add_callback(AfterToolCallEvent, self.after_call)

    def after_call(self, event: AfterToolCallEvent):
        failed = not isinstance(event.result, dict) or event.result.get('status') == 'error'
        if failed:
            self.failed = True
        elif event.tool_use['name'].startswith('stage_'):
            self.failed = False


class ModelCallLimit(HookProvider):
    """Bound tool loops and API quota independently of model instructions."""

    def __init__(self, maximum=4):
        self.maximum = maximum
        self.calls = 0

    def register_hooks(self, registry: HookRegistry):
        registry.add_callback(BeforeModelCallEvent, self.before_call)

    def before_call(self, event: BeforeModelCallEvent):
        self.calls += 1
        if self.calls > self.maximum:
            raise ValueError("The assistant needed too many tool rounds. Retry your saved message.")


def make_agent(prompt, tools=(), *, model=None, hooks=()):
    return Agent(
        model=model if model is not None else FallbackGeminiModel(timeout_seconds=45),
        system_prompt=prompt, tools=list(tools), hooks=list(hooks),
        callback_handler=None, retry_strategy=None,
        tool_executor=SequentialToolExecutor(),
    )


async def generate_chat_json(system_prompt, conversation_history, user_text, *,
                             timeout_seconds=None, max_output_tokens=1536, temperature=0.7):
    """Run a bounded structured Strands turn for voice and domain services.

    Device and memory effects stay in validated domain services, after complete
    JSON output. No partial model response or failed attempt can execute them.
    """
    model = FallbackGeminiModel(timeout_seconds=timeout_seconds,
        max_output_tokens=max_output_tokens, buffered=True, json_output=True,
        temperature=temperature)
    agent = make_agent(system_prompt, model=model, hooks=[ModelCallLimit(maximum=1)])
    agent.messages = [{'role': 'user' if m['role'] == 'user' else 'assistant',
                       'content': [{'text': m['text']}]} for m in conversation_history]
    result = await agent.invoke_async(user_text)
    value = json.loads(str(result))
    if not isinstance(value, dict):
        raise ValueError('The agent returned an invalid structured response.')
    return value
