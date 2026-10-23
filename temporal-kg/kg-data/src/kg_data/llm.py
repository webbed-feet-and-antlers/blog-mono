"""LLM calls via OpenRouter (OpenAI-compatible API).

Mirrors study-app/backend/app/llm.py: the openai SDK pointed at OpenRouter's
base URL, one key across all providers. On top of the study-app client this
adds `structured()` — a pydantic-validated generation with alias
normalization and a single repair pass (models rename JSON keys; we nudge
with the validation errors rather than discarding long generations).
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, TypeVar

from openai import AsyncOpenAI
from pydantic import BaseModel, ValidationError

from .config import settings

logger = logging.getLogger(__name__)

_client: AsyncOpenAI | None = None
T = TypeVar("T", bound=BaseModel)


class StructuredGenerationError(RuntimeError):
    """The model never produced output validating as the requested model."""


def _get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        if not settings.openrouter_api_key:
            raise RuntimeError(
                "OPENROUTER_API_KEY is not set. Copy .env.example to .env "
                "and add your key from https://openrouter.ai/keys"
            )
        import httpx

        _client = AsyncOpenAI(
            base_url=settings.openrouter_base_url,
            api_key=settings.openrouter_api_key,
            # the SDK default is 600s — a hung request wedges a concurrency
            # slot for ten minutes; 60s fails fast into the retry loop
            timeout=httpx.Timeout(60.0, connect=10.0),
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

    Retries on empty responses (common on cheap OpenRouter models under
    rate limits) with exponential backoff spanning ~15s.
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
        kwargs["response_format"] = {"type": "json_object"}

    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        try:
            response = await client.chat.completions.create(**kwargs)
            content = response.choices[0].message.content or ""
            if content.strip():
                return content
            logger.warning(
                "LLM returned empty response (attempt %d/%d), retrying…",
                attempt + 1, retries + 1,
            )
        except Exception as exc:
            last_exc = exc
            logger.warning(
                "LLM call failed (attempt %d/%d): %s",
                attempt + 1, retries + 1, exc,
            )
        if attempt < retries:
            await asyncio.sleep(min(8.0, 2.0 ** attempt))

    if last_exc:
        raise last_exc
    return ""


async def chat_json(
    messages: list[dict[str, str]],
    *,
    temperature: float = 0.3,
    max_tokens: int | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    """Chat expecting a JSON object response. Parses and returns the dict.

    Retries once with an explicit "ONLY valid JSON" nudge when the model
    wraps output in prose or fences; falls back to extracting the first
    balanced {...} block.
    """
    raw = await chat(
        messages, json_mode=True, temperature=temperature,
        max_tokens=max_tokens, model=model,
    )
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        try:
            return _extract_json_object(raw)
        except (ValueError, json.JSONDecodeError):
            pass

        logger.warning("LLM returned unparseable JSON (%.40r…), retrying with nudge", raw)
        nudged = list(messages)
        nudged[0] = {
            **nudged[0],
            "content": nudged[0]["content"]
            + "\n\nIMPORTANT: respond with ONLY a single valid JSON object, "
            "no markdown, no prose, no code fences.",
        }
        await asyncio.sleep(1)
        raw = await chat(
            nudged, json_mode=True, temperature=temperature,
            max_tokens=max_tokens, model=model,
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


_CAMEL_RE = re.compile(r"(?<!^)(?=[A-Z])")


def _normalize_aliases(obj: Any) -> Any:
    """Recursively normalize dict keys: camelCase → snake_case, stray '?'
    markers stripped (models sometimes echo optional-field annotations into
    the key itself, e.g. "title?" → "title")."""
    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for k, v in obj.items():
            if isinstance(k, str):
                k = _CAMEL_RE.sub("_", k.strip("?")).lower()
                k = k.replace("-", "_").replace(" ", "_")
            out[k] = _normalize_aliases(v)
        return out
    if isinstance(obj, list):
        return [_normalize_aliases(v) for v in obj]
    return obj


def render_prompt_skeleton(model_cls: type[BaseModel]) -> str:
    """Compact field skeleton injected into prompts so the model sees exact keys."""
    schema = model_cls.model_json_schema()
    defs = schema.get("$defs", {})

    def shape(cls_schema: dict[str, Any]) -> str:
        props = cls_schema.get("properties", {})
        required = set(cls_schema.get("required", []))
        parts = []
        for name, spec in props.items():
            typ = spec.get("type", "")
            if typ == "array":
                items = spec.get("items", {})
                if "$ref" in items:
                    typ = f"array[{defs[items['$ref'].split('/')[-1]].get('title', 'object')}]"
                else:
                    typ = f"array[{items.get('type', '?')}]"
            elif "$ref" in spec:
                typ = defs[spec["$ref"].split("/")[-1]].get("title", "object")
            elif spec.get("anyOf"):
                typ = "|".join(
                    (o.get("type") or o.get("$ref", "?").split("/")[-1])
                    for o in spec["anyOf"]
                )
            if name not in required:
                typ += " (optional)"
            parts.append(f'"{name}": {typ}')
        return "{" + ", ".join(parts) + "}"

    return shape(schema)


async def structured(
    model_cls: type[T],
    system: str,
    user: str,
    *,
    temperature: float = 0.4,
    max_tokens: int | None = None,
) -> T:
    """Generate a pydantic-validated object.

    Defends against key drift three ways: the prompt embeds the exact field
    skeleton, keys are camelCase-normalized before validation, and on
    ValidationError we retry ONCE with the errors appended. Never silently
    discards generations.
    """
    skeleton = render_prompt_skeleton(model_cls)
    messages = [
        {"role": "system", "content": (
            f"{system}\n\nRespond with a single JSON object using EXACTLY these "
            f"keys (snake_case):\n{skeleton}"
        )},
        {"role": "user", "content": user},
    ]

    for attempt in range(2):
        try:
            raw = await chat_json(
                messages, temperature=temperature, max_tokens=max_tokens
            )
            return model_cls.model_validate(_normalize_aliases(raw))
        except (ValidationError, ValueError, json.JSONDecodeError) as exc:
            if attempt == 0:
                logger.warning(
                    "Validation/parse failed for %s (%s); retrying with errors",
                    model_cls.__name__,
                    exc.errors()[0].get("msg", str(exc)[:80])
                    if isinstance(exc, ValidationError) else str(exc)[:120],
                )
                messages = [
                    {**messages[0]},
                    {"role": "user", "content": user
                     + "\n\nYour previous reply was invalid or truncated — "
                     "fix these problems:\n" + str(exc)[:500]},
                ]
            else:
                raise StructuredGenerationError(
                    f"Could not generate valid {model_cls.__name__}: "
                    f"{str(exc)[:300]}"
                ) from exc
    raise StructuredGenerationError("unreachable")
