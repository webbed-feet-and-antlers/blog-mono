"""LLM calls via OpenRouter (OpenAI-compatible API).

OpenRouter exposes an OpenAI-compatible /chat/completions endpoint, so we use
the openai SDK pointed at OpenRouter's base URL. One API key works across all
providers (Anthropic, OpenAI, Google, DeepSeek, open-source models, etc.) — to
swap models, just change OPENROUTER_MODEL in the env.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from openai import AsyncOpenAI

from .config import settings

logger = logging.getLogger(__name__)

_client: AsyncOpenAI | None = None


def _get_client() -> AsyncOpenAI:
    """Lazily build the singleton OpenAI client configured for OpenRouter."""
    global _client
    if _client is None:
        if not settings.openrouter_api_key:
            raise RuntimeError(
                "OPENROUTER_API_KEY is not set. Copy .env.example to .env "
                "and add your key from https://openrouter.ai/keys"
            )
        _client = AsyncOpenAI(
            base_url=settings.openrouter_base_url,
            api_key=settings.openrouter_api_key,
        )
    return _client


async def chat(
    messages: list[dict[str, str]],
    *,
    json_mode: bool = False,
    temperature: float = 0.3,
    max_tokens: int | None = None,
    retries: int = 4,
    model: str | None = None,
) -> str:
    """Run a chat completion and return the assistant's text content.

    Retries on empty responses (a common transient failure on cheap/free LLM
    endpoints like OpenRouter's free tier — the model returns "" on rate limits
    or timeouts). Exponential backoff: 1s, 2s, 4s, 8s — rate-limit storms on
    the free tier can outlast the old linear 1s/2s windows, so the curve now
    spans ~15s across five attempts. `model` optionally overrides
    settings.openrouter_model for a single call (the evals judge uses this).
    """
    client = _get_client()
    kwargs: dict[str, Any] = {
        "model": model or settings.openrouter_model,
        "messages": messages,
        "temperature": temperature,
    }
    if max_tokens is not None:
        kwargs["max_tokens"] = max_tokens
    if json_mode:
        # OpenRouter supports OpenAI's response_format for JSON mode on most
        # models; the underlying provider may or may not honor it.
        kwargs["response_format"] = {"type": "json_object"}

    import asyncio as _asyncio

    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        try:
            response = await client.chat.completions.create(**kwargs)
            content = response.choices[0].message.content or ""
            if content.strip():
                return content
            # Empty response — retry after backoff.
            logger.warning(
                "LLM returned empty response (attempt %d/%d), retrying…",
                attempt + 1,
                retries + 1,
            )
        except Exception as exc:
            last_exc = exc
            logger.warning(
                "LLM call failed (attempt %d/%d): %s",
                attempt + 1,
                retries + 1,
                exc,
            )
        if attempt < retries:
            await _asyncio.sleep(min(8.0, 2.0 ** attempt))

    if last_exc:
        raise last_exc
    return ""  # all retries returned empty


async def chat_json(
    messages: list[dict[str, str]],
    *,
    temperature: float = 0.3,
    max_tokens: int | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    """Chat expecting a JSON object response. Parses and returns the dict.

    Robust against two failure modes common on cheap LLM endpoints:
    1. Empty responses — `chat` already retries these internally.
    2. Non-JSON output (prose wrapping, markdown fences) — we retry the call
       with an explicit "return ONLY valid JSON" nudge before giving up.

    Falls back to extracting the first {...} block if the model wraps the JSON
    in prose or doesn't honour json mode.
    """
    import asyncio as _asyncio

    raw = await chat(
        messages,
        json_mode=True,
        temperature=temperature,
        max_tokens=max_tokens,
        model=model,
    )
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        try:
            return _extract_json_object(raw)
        except (ValueError, json.JSONDecodeError):
            pass

        # The model didn't return parseable JSON. Retry once with a nudge
        # appended to the system message.
        logger.warning(
            "LLM returned unparseable JSON (%.40r…), retrying with nudge", raw
        )
        nudged = list(messages)
        nudged[0] = {
            **nudged[0],
            "content": nudged[0]["content"]
            + "\n\nIMPORTANT: respond with ONLY a single valid JSON object, "
            "no markdown, no prose, no code fences.",
        }
        await _asyncio.sleep(1)
        raw = await chat(
            nudged,
            json_mode=True,
            temperature=temperature,
            max_tokens=max_tokens,
            model=model,
        )
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return _extract_json_object(raw)
    raw = await chat(
        messages,
        json_mode=True,
        temperature=temperature,
        max_tokens=max_tokens,
        model=model,
    )
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return _extract_json_object(raw)


def _extract_json_object(text: str) -> dict[str, Any]:
    """Best-effort: pull the first balanced {...} block out of `text`."""
    start = text.find("{")
    if start == -1:
        raise ValueError(f"No JSON object found in LLM output: {text[:200]!r}")
    depth = 0
    for i in range(start, len(text)):
        c = text[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return json.loads(text[start : i + 1])
    raise ValueError(f"Unbalanced JSON in LLM output: {text[:200]!r}")
