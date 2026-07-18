"""Async DeepSeek API client with retry, JSON-mode, validation, and repair.

Endpoint is OpenAI-compatible: ``POST https://api.deepseek.com/v1/chat/completions``.
Auth is ``Authorization: Bearer $DEEPSEEK_API_KEY``.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import httpx
from pydantic import BaseModel, ValidationError
from tenacity import (
    AsyncRetrying,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential_jitter,
)

from .cache import DiskCache, key_for
from .prompts import REPAIR_SYSTEM

logger = logging.getLogger(__name__)

ENDPOINT = "https://api.deepseek.com/v1/chat/completions"

# Status codes worth retrying. 400/401/403 are caller errors — never retry.
RETRY_STATUS = {408, 409, 429, 500, 502, 503, 504}

DEFAULT_TIMEOUT = httpx.Timeout(connect=10.0, read=120.0, write=10.0, pool=30.0)


class LLMJsonError(Exception):
    """Raised when the model output cannot be parsed into the requested schema."""


class _RetryableHTTPError(Exception):
    """Internal wrapper used to drive tenacity retry on retryable status codes."""

    def __init__(self, status_code: int, body: str) -> None:
        super().__init__(f"HTTP {status_code}: {body[:200]}")
        self.status_code = status_code
        self.body = body


def _build_payload(
    *,
    model: str,
    messages: list[dict[str, str]],
    temperature: float,
    max_tokens: int,
) -> dict[str, Any]:
    return {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "response_format": {"type": "json_object"},
    }


def _extract_content(resp_json: dict[str, Any]) -> tuple[str, int, int]:
    """Return (content, prompt_tokens, completion_tokens) from an OpenAI-shape response."""
    choice = (resp_json.get("choices") or [{}])[0]
    msg = choice.get("message") or {}
    content = msg.get("content") or ""
    usage = resp_json.get("usage") or {}
    return (
        content,
        int(usage.get("prompt_tokens", 0) or 0),
        int(usage.get("completion_tokens", 0) or 0),
    )


async def _post_once(
    client: httpx.AsyncClient,
    *,
    api_key: str,
    payload: dict[str, Any],
) -> tuple[str, int, int]:
    """Single HTTP attempt. Raises _RetryableHTTPError on retryable status."""
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    resp = await client.post(ENDPOINT, headers=headers, json=payload)

    if resp.status_code in RETRY_STATUS:
        raise _RetryableHTTPError(resp.status_code, resp.text)

    if resp.status_code >= 400:
        # Non-retryable HTTP error — surface to caller as a hard failure.
        resp.raise_for_status()

    return _extract_content(resp.json())


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, _RetryableHTTPError):
        return True
    # Transport / timeout errors from httpx: retry.
    if isinstance(exc, (httpx.TimeoutException, httpx.TransportError, httpx.NetworkError)):
        return True
    return False


def _retrying(*, max_attempts: int = 6) -> AsyncRetrying:
    """A tenacity controller. Honors Retry-After via 429 backoff is best-effort
    (tenacity's wait_exponential_jitter does not inspect response headers; the
    server-side backoff is generally adequate)."""
    return AsyncRetrying(
        retry=retry_if_exception(_is_retryable),
        wait=wait_exponential_jitter(initial=1, max=60),
        stop=stop_after_attempt(max_attempts),
        reraise=True,
    )


async def call_json(
    client: httpx.AsyncClient,
    *,
    model: str,
    messages: list[dict[str, str]],
    temperature: float,
    max_tokens: int,
    response_model: type[BaseModel],
    sem: asyncio.Semaphore,
    cache: DiskCache,
    api_key: str,
    use_cache: bool = True,
    max_repair_attempts: int = 1,
) -> BaseModel:
    """Call DeepSeek, parse the JSON response, validate against ``response_model``.

    On JSON/validation failure, append a repair system message and retry once.
    Retries transport/retryable-HTTP errors via tenacity (honors 429 etc.).
    Cache hit short-circuits — no semaphore acquisition, no API call.
    """
    key = key_for(
        model=model, messages=messages, temperature=temperature, max_tokens=max_tokens
    )
    if use_cache:
        hit = cache.get(key)
        if hit is not None:
            parsed = hit.get("parsed")
            if parsed is not None:
                try:
                    return response_model.model_validate(parsed)
                except ValidationError as e:
                    logger.debug("cache hit for %s failed revalidation: %s", key[:8], e)

    # Build payload + apply retry policy once per call. We hold the semaphore
    # for the entire duration (including backoff) so concurrency stays bounded.
    async with sem:
        parsed_model, raw_content, prompt_tokens, completion_tokens = await _call_with_repair(
            client,
            api_key=api_key,
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            response_model=response_model,
            max_repair_attempts=max_repair_attempts,
        )

    cache.put(
        key=key,
        model=model,
        temperature=temperature,
        request_messages=messages,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        raw_content=raw_content,
        parsed=parsed_model.model_dump(),
    )
    return parsed_model


async def _call_with_repair(
    client: httpx.AsyncClient,
    *,
    api_key: str,
    model: str,
    messages: list[dict[str, str]],
    temperature: float,
    max_tokens: int,
    response_model: type[BaseModel],
    max_repair_attempts: int,
) -> tuple[BaseModel, str, int, int]:
    """Issue the API call (with tenacity retry) and try to parse/validate.

    On parse/validation failure, append a repair message and try again up to
    ``max_repair_attempts`` times. The returned raw_content / token counts
    reflect the successful call (or the last failed call).
    """
    attempt_messages = list(messages)
    last_error = ""
    last_raw = ""
    last_ptoks = last_ctoks = 0

    for attempt in range(max_repair_attempts + 1):
        payload = _build_payload(
            model=model,
            messages=attempt_messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )

        try:
            async for attempt_state in _retrying():
                with attempt_state:
                    raw, ptoks, ctoks = await _post_once(
                        client, api_key=api_key, payload=payload
                    )
        except (_RetryableHTTPError, httpx.HTTPError):
            # Retries exhausted (reraise=True) — surface to caller.
            raise

        last_raw, last_ptoks, last_ctoks = raw, ptoks, ctoks

        # Parse JSON content.
        try:
            parsed_json = json.loads(raw)
        except json.JSONDecodeError as e:
            last_error = f"JSON decode error: {e}"
            logger.warning("repair: %s (attempt %d)", last_error, attempt + 1)
            attempt_messages = list(messages) + [
                {"role": "assistant", "content": raw},
                {"role": "system", "content": REPAIR_SYSTEM.format(error=last_error)},
            ]
            continue

        # Validate against the pydantic schema.
        try:
            parsed_model = response_model.model_validate(parsed_json)
            return parsed_model, last_raw, last_ptoks, last_ctoks
        except ValidationError as e:
            last_error = f"pydantic validation error: {e}"
            logger.warning("repair: %s (attempt %d)", last_error, attempt + 1)
            attempt_messages = list(messages) + [
                {"role": "assistant", "content": raw},
                {"role": "system", "content": REPAIR_SYSTEM.format(error=last_error)},
            ]
            continue

    raise LLMJsonError(f"Failed after {max_repair_attempts + 1} attempt(s): {last_error}")
