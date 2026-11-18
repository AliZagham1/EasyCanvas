"""OpenAI answer-generation boundary; no retrieval, database, or UI logic."""

import asyncio
import json
import os
import random
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

from backend.app import consent_service


# Question understanding is a short call: fail fast, then retry transient errors (rate limits, timeouts, 5xx) with backoff.
UNDERSTANDING_TIMEOUT = 25
RETRY_DELAYS = (1.0, 2.5)
MAX_RETRY_AFTER = 8.0
_sleep = asyncio.sleep


def _jitter():
    return random.uniform(0, 0.4)


def is_transient(error):
    """Worth retrying: network trouble, timeouts, rate limits and server errors. Never bad keys, quota or bad requests."""
    from openai import APIConnectionError, APIStatusError
    if isinstance(error, APIConnectionError):          # includes timeouts
        return True
    if isinstance(error, APIStatusError):
        if getattr(error, "code", None) == "insufficient_quota":
            return False
        return error.status_code in (408, 409, 429) or error.status_code >= 500
    return False


def _retry_after(error):
    try:
        value = float(error.response.headers.get("retry-after"))
    except (AttributeError, TypeError, ValueError):
        return None
    return min(max(value, 0.0), MAX_RETRY_AFTER)


class LLMProviderError(Exception):
    """Safe failure category without raw response bodies or credentials."""


class LLMRefusalError(LLMProviderError):
    """The provider explicitly declined the request."""


def answer_schema(source_ids):
    """Constrain the response shape and citation vocabulary for this request."""
    source_item = {"type": "string", "enum": source_ids} if source_ids else {"type": "string"}
    return {
        "type": "object", "additionalProperties": False,
        "properties": {
            "status": {"type": "string", "enum": ["answered", "insufficient_context", "needs_structured_data"]},
            "blocks": {"type": "array", "items": {
                "type": "object", "additionalProperties": False,
                "properties": {"text": {"type": "string"},
                               "source_ids": {"type": "array", "items": source_item}},
                "required": ["text", "source_ids"],
            }},
            "limitations": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["status", "blocks", "limitations"],
    }


class OpenAIAnswerProvider:
    """Make stateless Responses API calls using the existing local API key."""

    def __init__(self, model=None, api_key=None, usage_recorder=None):
        load_dotenv(Path(__file__).resolve().parents[2] / ".env")
        consent_service.require_ai_consent(getattr(usage_recorder, "store", None))
        self.model = model if model is not None else os.getenv("EASYCANVAS_ANSWER_MODEL", "gpt-6-luna")
        if not isinstance(self.model, str) or not self.model.strip():
            raise ValueError("Answer model must be a nonempty model identifier.")
        from backend.app.openai_key_service import resolve_api_key
        self._api_key = api_key or resolve_api_key(getattr(usage_recorder, "store", None))[0]
        if not self._api_key:
            raise ValueError("Add your OpenAI API key in Settings (or set OPENAI_API_KEY in .env) before asking questions.")
        from backend.app.api_usage_service import UsageRecorder
        self.usage_recorder = usage_recorder if usage_recorder is not None else UsageRecorder()

    def _require_consent(self):
        """Checked before every provider request; unknown consent means denied."""
        consent_service.require_ai_consent(getattr(self.usage_recorder, "store", None))

    def generate(self, instructions, course_id, question, passages):
        """Return parsed JSON and usage; never follow tools or persist a conversation."""
        self._require_consent()
        payload = {
            "model": self.model, "store": False, "instructions": instructions,
            "input": json.dumps({"selected_course_id": course_id, "question": question, "passages": passages}),
            "reasoning": {"effort": "low"}, "max_output_tokens": 2400,
            "text": {"format": {"type": "json_schema", "name": "course_answer", "strict": True,
                                "schema": answer_schema([p["id"] for p in passages])}},
        }
        for attempt in range(3):
            if attempt:
                self._require_consent()  # a retry is a new request
            try:
                response = requests.post(
                    "https://api.openai.com/v1/responses", json=payload,
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    timeout=(10, 90), allow_redirects=False,
                )
            except (requests.Timeout, requests.ConnectionError):
                if self.usage_recorder:
                    self.usage_recorder.record({}, feature='study_assistant', operation='responses.transport_error', model=self.model)
                if attempt == 2:
                    raise LLMProviderError("Answer provider connection failed after retries.") from None
                time.sleep(2 ** attempt)
                continue
            except requests.RequestException:
                if self.usage_recorder:
                    self.usage_recorder.record({}, feature='study_assistant', operation='responses.transport_error', model=self.model)
                raise LLMProviderError("Answer provider request failed.") from None
            try:
                if self.usage_recorder:
                    try:
                        usage_body = response.json()
                    except ValueError:
                        usage_body = {}
                    self.usage_recorder.record(usage_body, feature='study_assistant', operation='responses.create',
                                              model=self.model, request_id=response.headers.get('x-request-id'))
                if response.status_code != 200:
                    try:
                        error = response.json().get("error", {})
                        quota = isinstance(error, dict) and "insufficient_quota" in (error.get("type"), error.get("code"))
                    except (ValueError, AttributeError, TypeError):
                        quota = False
                    if quota:
                        raise LLMProviderError("OpenAI API quota unavailable. Check API billing/project credits.")
                if response.status_code in (429, 500, 502, 503, 504) and attempt < 2:
                    try:
                        delay = float(response.headers.get("Retry-After", 2 ** attempt))
                    except (ValueError, TypeError):
                        delay = 2 ** attempt
                    time.sleep(max(0, min(delay, 10)))
                    continue
                if response.status_code != 200:
                    raise LLMProviderError(f"Answer provider returned HTTP {response.status_code}.")
                try:
                    body = response.json()
                    if body.get("status") != "completed":
                        raise LLMProviderError("Answer provider did not complete its response.")
                    texts = []
                    for item in body["output"]:
                        if item.get("type") == "reasoning":
                            continue
                        if item.get("type") != "message" or item.get("role") != "assistant":
                            raise LLMProviderError("Unexpected answer provider output.")
                        for part in item["content"]:
                            if part.get("type") == "refusal":
                                raise LLMRefusalError("The answer provider declined this request.")
                            if part.get("type") != "output_text" or not isinstance(part.get("text"), str):
                                raise LLMProviderError("Unexpected answer provider content.")
                            texts.append(part["text"])
                    if len(texts) != 1:
                        raise LLMProviderError("Answer provider returned an unexpected message count.")
                    answer = json.loads(texts[0])
                    usage = body.get("usage") or {}
                    tokens = {key: usage.get(key) for key in ("input_tokens", "output_tokens")}
                    if any(value is not None and (type(value) is not int or value < 0) for value in tokens.values()):
                        raise ValueError("Invalid usage")
                except (ValueError, KeyError, TypeError, AttributeError):
                    raise LLMProviderError("Answer provider returned an invalid response.") from None
                return {"answer": answer, "usage": tokens, "model": self.model}
            finally:
                response.close()

    def generate_general(self, instructions, question):
        """Use the answer model without course context or document citations."""
        return self.generate(instructions, None, question, [])

    async def _with_retries(self, call, *, stage, model):
        """Run `call()`; retry transient provider errors with backoff, recording each retry, failure and recovery."""
        from openai import OpenAIError
        from backend.app import assistant_events
        store = getattr(self.usage_recorder, "store", None) if self.usage_recorder else None
        attempt = 0
        while True:
            attempt += 1
            try:
                result = await call()
            except OpenAIError as error:
                if not is_transient(error) or attempt > len(RETRY_DELAYS):
                    assistant_events.record(store, stage, "failed", error=error, attempt=attempt, model=model)
                    raise
                assistant_events.record(store, stage, "retry", error=error, attempt=attempt, model=model)
                delay = _retry_after(error)
                await _sleep((RETRY_DELAYS[attempt - 1] if delay is None else delay) + _jitter())
                continue
            if attempt > 1:
                assistant_events.record(store, stage, "recovered", attempt=attempt, model=model)
            return result

    async def rewrite_query(self, course_id, question, conversation_context):
        """Resolve references only; return no answer and select no invented IDs."""
        from openai import AsyncOpenAI, OpenAIError
        self._require_consent()
        model = os.getenv("EASYCANVAS_QUERY_MODEL") or self.model
        from backend.app.query_understanding_service import (
            QUERY_INSTRUCTIONS, PlanRejected, checked_understanding, log_plan_event, query_schema)
        schema = query_schema()
        instructions = QUERY_INSTRUCTIONS
        accounted = False
        try:
            # The library's own single retry is replaced by _with_retries (backoff, Retry-After, saved diagnostics).
            async with AsyncOpenAI(api_key=self._api_key, timeout=UNDERSTANDING_TIMEOUT, max_retries=0) as client:
                response = await self._with_retries(lambda: client.responses.create(
                    model=model, store=False, instructions=instructions, max_output_tokens=1100,
                    reasoning={"effort": "low"},
                    input=json.dumps({"course_id": course_id, "question": question, "conversation_context": conversation_context}),
                    text={"format": {"type": "json_schema", "name": "retrieval_query", "strict": True, "schema": schema}},
                ), stage="query_understanding", model=model)
                if self.usage_recorder:
                    self.usage_recorder.record(response, feature='query_understanding', operation='responses.create', model=model)
                    accounted = True
                if response.status != "completed":
                    reason = getattr(getattr(response, "incomplete_details", None), "reason", None)
                    log_plan_event("incomplete", condition=":".join(str(part)[:40] for part in (response.status, reason) if part), model=model)
                    raise LLMProviderError("Query understanding did not complete.")
                result = json.loads(response.output_text)
                # Recoverable optional-field variations are repaired; everything else is rejected and logged.
                return checked_understanding(result, question, model=model)
        except PlanRejected:
            raise LLMProviderError("Unable to understand the question. Please retry or name the assignment.") from None
        except (OpenAIError, ValueError, TypeError, KeyError, AttributeError) as error:
            # The error class only: provider messages can echo request content.
            log_plan_event("failed", condition=type(error).__name__, model=model)
            raise LLMProviderError("Unable to understand the question. Please retry or name the assignment.") from None
        finally:
            if self.usage_recorder and not accounted:
                self.usage_recorder.record({}, feature='query_understanding', operation='responses.usage_unavailable', model=model)

    async def stream_generate(self, instructions, course_id, question, passages, conversation_context):
        """Decode growing structured text from real Responses API stream events.

        Partial JSON is read with Pydantic's parser. Only text fields leave this
        boundary during generation; citation IDs remain withheld until validation.
        """
        from openai import AsyncOpenAI, OpenAIError
        from pydantic_core import from_json
        self._require_consent()
        watch = consent_service.ConsentWatch(getattr(self.usage_recorder, "store", None))
        text, previous, completed = "", [], False
        accounted, response_id = False, None
        try:
            async with AsyncOpenAI(api_key=self._api_key, timeout=90, max_retries=1) as client:
                async with client.responses.stream(
                    model=self.model, store=False, instructions=instructions,
                    input=json.dumps({"selected_course_id": course_id, "question": question,
                                      "conversation_context": conversation_context, "passages": passages}),
                    reasoning={"effort": "low"}, max_output_tokens=4000,
                    text={"format": {"type": "json_schema", "name": "course_answer", "strict": True,
                                     "schema": answer_schema([p["id"] for p in passages])}},
                ) as stream:
                    async for event in stream:
                        watch.check()  # withdrawal stops the stream at the next event; the open request is then closed
                        if event.type == 'response.created':
                            response_id = event.response.id
                        if event.type in ('response.completed', 'response.failed', 'response.incomplete') and self.usage_recorder:
                            self.usage_recorder.record(event.response, feature='study_assistant', operation='responses.stream', model=self.model)
                            accounted = True
                        if event.type == "response.refusal.delta":
                            raise LLMRefusalError("The answer provider declined this request.")
                        if event.type in ("error", "response.failed", "response.incomplete"):
                            raise LLMProviderError("Answer generation did not complete.")
                        if event.type == "response.output_text.delta":
                            text += event.delta
                            if len(text.encode()) > 100000:
                                raise LLMProviderError("Answer stream exceeded its size limit.")
                            try:
                                partial = from_json(text, allow_partial="trailing-strings")
                            except ValueError:
                                continue
                            if partial.get("status") == "answered":
                                blocks = [block.get("text", "") for block in partial.get("blocks", []) if isinstance(block, dict)]
                                if blocks != previous:
                                    previous = blocks
                                    yield {"event": "answer_delta", "data": {"blocks": blocks}}
                        if event.type == "response.completed":
                            if event.response.status != "completed":
                                raise LLMProviderError("Answer provider did not complete.")
                            completed = True
                            usage = event.response.usage
                            yield {"event": "complete", "data": {"answer": json.loads(text), "model": self.model,
                                   "usage": {"input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens} if usage else None}}
            if not completed:
                raise LLMProviderError("Answer stream was interrupted.")
        except (OpenAIError, ValueError, TypeError, KeyError, AttributeError):
            raise LLMProviderError("The answer stream failed. Please retry.") from None
        finally:
            if self.usage_recorder and not accounted:
                self.usage_recorder.record({}, feature='study_assistant', operation='responses.stream_usage_unavailable', model=self.model, request_id=response_id)

    async def stream_general(self, instructions, question, conversation_context=None):
        """Stream a concept answer without supplying selected-course history."""
        recent = (conversation_context or {}).get('recent_turns', [])[-3:]
        async for event in self.stream_generate(instructions, None, question, [], {'recent_turns': recent}):
            yield event
