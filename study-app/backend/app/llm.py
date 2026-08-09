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
) -> str:
    """Run a chat completion and return the assistant's text content."""
    client = _get_client()
    kwargs: dict[str, Any] = {
        "model": settings.openrouter_model,
        "messages": messages,
        "temperature": temperature,
    }
    if max_tokens is not None:
        kwargs["max_tokens"] = max_tokens
    if json_mode:
        # OpenRouter supports OpenAI's response_format for JSON mode on most
        # models; the underlying provider may or may not honor it.
        kwargs["response_format"] = {"type": "json_object"}

    response = await client.chat.completions.create(**kwargs)
    return response.choices[0].message.content or ""


async def chat_json(
    messages: list[dict[str, str]],
    *,
    temperature: float = 0.3,
    max_tokens: int | None = None,
) -> dict[str, Any]:
    """Chat expecting a JSON object response. Parses and returns the dict.

    Falls back to extracting the first {...} block if the model wraps the JSON
    in prose or doesn't honour json mode.
    """
    raw = await chat(
        messages,
        json_mode=True,
        temperature=temperature,
        max_tokens=max_tokens,
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
