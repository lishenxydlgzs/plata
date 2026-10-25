"""Shared Strands runtime with the application's Gemini fallback policy."""

import asyncio
import logging
import json
import time
import uuid

from strands import Agent
from strands.hooks import AfterToolCallEvent, HookProvider, HookRegistry
from strands.models.gemini import GeminiModel
from strands.tools.executors import SequentialToolExecutor

from .llm import get_client, get_models, get_model_timeout_seconds, _is_fallback_error

logger = logging.getLogger(__name__)


class FallbackGeminiModel(GeminiModel):
    """Retry model calls before visible output, never replay an agent/tool run."""

    def __init__(self, *, timeout_seconds=None, max_output_tokens=4096, buffered=False, json_output=False, temperature=0.7,
                 response_schema=None, diagnostic_id=None, phase='initial'):
        self.models = get_models()
        self.buffered = buffered
        self.timeout_seconds = timeout_seconds or get_model_timeout_seconds()
        self.diagnostic_id = diagnostic_id
        self.phase = phase
        self.attempts = 0
        super().__init__(client=get_client(), model_id=self.models[0],
                         params={"max_output_tokens": max_output_tokens, "temperature": temperature,
                                 **({"response_mime_type": "application/json"} if json_output else {}),
                                 **({"response_json_schema": response_schema} if response_schema is not None else {})})

    async def stream(self, *args, **kwargs):
        for index, model_id in enumerate(self.models):
            self.update_config(model_id=model_id)
            started = False
            prelude = []
            self.attempts += 1
            attempt_started = time.monotonic()
            stop_reason = None
            usage = {}
            outcome = 'completed'
            error_type = None
            try:
                async with asyncio.timeout(self.timeout_seconds):
                    async for event in super().stream(*args, **kwargs):
                        if 'messageStop' in event:
                            stop_reason = event['messageStop'].get('stopReason')
                        if 'metadata' in event:
                            usage = event['metadata'].get('usage', {})
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
            except BaseException as error:
                outcome = 'failed'
                error_type = type(error).__name__
                if not isinstance(error, Exception):
                    raise
                if started or index == len(self.models) - 1 or not (
                    isinstance(error, TimeoutError) or _is_fallback_error(error)
                    or (error.__cause__ is not None and _is_fallback_error(error.__cause__))
                ):
                    raise
                logger.warning("Gemini model %s unavailable; trying configured fallback", model_id)
            finally:
                if self.diagnostic_id:
                    logger.info('Model attempt: %s', json.dumps({
                        'diagnostic_id': self.diagnostic_id, 'phase': self.phase,
                        'attempt': self.attempts, 'model': model_id, 'outcome': outcome,
                        'error_type': error_type, 'stop_reason': stop_reason,
                        'duration_ms': round((time.monotonic() - attempt_started) * 1000),
                        'max_output_tokens': self.config['params']['max_output_tokens'],
                        'timeout_seconds': self.timeout_seconds,
                        'json_mode': self.config['params'].get('response_mime_type') == 'application/json',
                        'input_tokens': usage.get('inputTokens'),
                        'output_tokens': usage.get('outputTokens'),
                        'total_tokens': usage.get('totalTokens'),
                    }, sort_keys=True))


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


def make_agent(prompt, tools=(), *, model=None, hooks=()):
    return Agent(
        model=model if model is not None else FallbackGeminiModel(timeout_seconds=45),
        system_prompt=prompt, tools=list(tools), hooks=list(hooks),
        callback_handler=None, retry_strategy=None,
        tool_executor=SequentialToolExecutor(),
    )


async def generate_chat_json(system_prompt, conversation_history, user_text, *,
                             timeout_seconds=None, max_output_tokens=1536, temperature=0.7, tools=(), log_response_on_error=False, response_schema=None):
    """Run a structured Strands turn for voice and domain services.

    Device and memory effects stay in validated domain services, after complete
    JSON output. No partial model response or failed attempt can execute them.
    Channel tools may queue authorized delivery during the turn; their durable
    effects are not rolled back if final JSON generation fails.
    """
    if response_schema is not None and tools:
        raise ValueError("Response schemas require a structured turn without tools.")
    diagnostic_id = uuid.uuid4().hex
    model = FallbackGeminiModel(timeout_seconds=timeout_seconds,
        max_output_tokens=max_output_tokens, buffered=True, json_output=not bool(tools),
        temperature=temperature, response_schema=response_schema, diagnostic_id=diagnostic_id)
    logger.info('Structured turn started: diagnostic_id=%s history_turns=%d tool_count=%d',
                diagnostic_id, len(conversation_history), len(tools))
    if tools:
        system_prompt += "\nUse the available tools as needed. After tool use, return ONLY the final JSON object in the documented response format, without markdown."
    agent = make_agent(system_prompt, tools=tools, model=model)
    agent.messages = [{'role': 'user' if m['role'] == 'user' else 'assistant',
                       'content': [{'text': m['text']}]} for m in conversation_history]
    try:
        return await _structured_result(agent, user_text, diagnostic_id, 'initial', model=model,
                                        log_response_on_error=log_response_on_error)
    except _StructuredResponseError:
        if not tools:
            raise
    # This turn disables JSON mode while tools are offered. Recover formatting
    # once with the full transcript, without replaying any tool or domain effect.
    logger.warning('Recovering invalid structured response: diagnostic_id=%s', diagnostic_id)
    recovery = make_agent(system_prompt, model=FallbackGeminiModel(
        timeout_seconds=timeout_seconds, max_output_tokens=max_output_tokens,
        buffered=True, json_output=True, temperature=temperature,
        diagnostic_id=diagnostic_id, phase='recovery'))
    recovery.messages = agent.messages
    return await _structured_result(recovery,
        'Return the final JSON object in the response format specified in the system '
        'prompt, based on the conversation and tool results above. Your previous '
        'response was not a valid JSON object. No tools are available in this '
        'formatting step. Do not invent tool results or claim unperformed actions.',
        diagnostic_id, 'recovery')


class _StructuredResponseError(ValueError):
    """Only final formatting failures may trigger a tool-free recovery."""


async def _structured_result(agent, user_text, diagnostic_id, phase, *, model=None, log_response_on_error=False):
    try:
        result = await agent.invoke_async(user_text)
    except Exception as error:
        logger.error('Structured invocation failed: diagnostic_id=%s phase=%s error_type=%s',
                     diagnostic_id, phase, type(error).__name__)
        raise
    text = str(result).strip()
    shape = ('empty' if not text else 'fenced' if text.startswith('```') else
             'object_start' if text.startswith('{') else 'array_start' if text.startswith('[') else 'other_text')
    details = {'diagnostic_id': diagnostic_id, 'phase': phase,
               'stop_reason': getattr(result, 'stop_reason', None),
               'response_chars': len(text), 'response_shape': shape,
               'model': model.config.get('model_id') if model else agent.model.config.get('model_id')}
    try:
        value = _response_object(result)
    except ValueError as error:
        details.update(error_type=type(error).__name__, error_message=str(error),
                       error_position=error.pos if isinstance(error, json.JSONDecodeError) else None)
        logger.error('Structured response rejected: %s', json.dumps(details, sort_keys=True))
        if log_response_on_error:
            logger.error('Rejected response excerpt (first 1000 chars): %s', json.dumps(text[:1000], ensure_ascii=True))
        raise _StructuredResponseError('The agent returned an invalid structured response.') from error
    logger.info('Structured response accepted: %s', json.dumps(details, sort_keys=True))
    return value


def _response_object(result):
    text = str(result).strip()
    if text.startswith("```json\n") and text.endswith("\n```"):
        text = text[len("```json\n"):-len("\n```")]
    value = json.loads(text)
    if not isinstance(value, dict):
        raise ValueError(
            f'The agent returned an invalid structured response: expected object, got {type(value).__name__}.')
    return value
