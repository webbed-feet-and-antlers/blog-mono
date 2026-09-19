"""LLM calls via OpenRouter (OpenAI-compatible API).

Retry + JSON-extraction semantics ported from study-app/backend/app/llm.py.
Adds completion_logprobs for teacher-forced surprisal scoring.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from openai import AsyncOpenAI

from .config import get_settings

logger = logging.getLogger(__name__)

_client: AsyncOpenAI | None = None


def _get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        settings = get_settings()
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
    top_p: float | None = None,
    max_tokens: int | None = None,
    retries: int = 4,
    model: str | None = None,
) -> str:
    """Chat completion → assistant text. Retries empty responses with
    exponential backoff (1s, 2s, 4s, 8s) — cheap endpoints return "" under
    rate limits."""
    client = _get_client()
    kwargs: dict[str, Any] = {
        "model": model or get_settings().models.draft,
        "messages": messages,
        "temperature": temperature,
    }
    if top_p is not None:
        kwargs["top_p"] = top_p
    if max_tokens is not None:
        kwargs["max_tokens"] = max_tokens
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}

    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        t0 = time.monotonic()
        try:
            response = await client.chat.completions.create(**kwargs)
            content = response.choices[0].message.content or ""
            took = time.monotonic() - t0
            if content.strip():
                logger.info(
                    "chat ok: model=%s attempt=%d/%d took=%.1fs in_msgs=%d "
                    "out_chars=%d",
                    kwargs["model"],
                    attempt + 1,
                    retries + 1,
                    took,
                    len(messages),
                    len(content),
                )
                return content
            logger.warning(
                "chat EMPTY response: model=%s attempt=%d/%d took=%.1fs",
                kwargs["model"],
                attempt + 1,
                retries + 1,
                took,
            )
        except Exception as exc:
            last_exc = exc
            logger.warning(
                "chat ERROR: model=%s attempt=%d/%d took=%.1fs exc=%r",
                kwargs["model"],
                attempt + 1,
                retries + 1,
                time.monotonic() - t0,
                exc,
            )
        if attempt < retries:
            await asyncio.sleep(min(8.0, 2.0**attempt))
    if last_exc:
        raise last_exc
    return ""


def _extract_json_object(text: str) -> dict[str, Any]:
    """Best-effort: pull the first balanced {...} block out of `text`."""
    start = text.find("{")
    if start == -1:
        raise ValueError(f"No JSON object found in LLM output: {text[:200]!r}")
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return json.loads(text[start : i + 1])
    raise ValueError(f"Unbalanced JSON in LLM output: {text[:200]!r}")


def chat_json_with(chat_fn):
    """Build a chat_json bound to an injectable chat function (for tests)."""

    async def _chat_json(
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.3,
        max_tokens: int | None = None,
        model: str | None = None,
    ) -> dict[str, Any]:
        raw = await chat_fn(
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
        nudged = list(messages)
        nudged[0] = {
            **nudged[0],
            "content": nudged[0]["content"]
            + "\n\nIMPORTANT: respond with ONLY a single valid JSON object, "
            "no markdown, no prose, no code fences.",
        }
        await asyncio.sleep(1)
        raw = await chat_fn(
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

    return _chat_json


chat_json = chat_json_with(chat)


_SCORE_PROMPT = """You are continuing an existing document. Predict and write
the author's EXACT next sentences — the words the author themselves would
write next, as close to verbatim as you can. Match their voice. Output ONLY
the continuation — no commentary, no quotation marks, no headings.

<document>
{doc}
</document>"""


async def chat_score(
    prefix: str,
    *,
    model: str | None = None,
    max_tokens: int = 300,
    temperature: float = 0.0,
    logprobs: bool = True,
) -> tuple[str, list[float]]:
    """Prefix-continuation scoring call → (continuation_text, token_logprobs).

    The scorer sees ONLY the document-so-far — the text being scored is
    never in the prompt, so its logprobs measure genuine predictability
    (the old legacy-/completions echo+logprobs path stopped working on
    OpenRouter: every model returns logprobs=null and logprobs>=1 requests
    are mistranslated — probed 2026-09). Logprobs are requested but
    optional; providers that omit them yield [] and the caller falls back
    to overlap-only scoring. Logprob params are omitted entirely when
    `logprobs=False` — some providers 400 on the parameter itself, which
    is how the observer model calls in. Logprobs are natural-log units.
    """
    client = _get_client()
    resolved = model or get_settings().models.scorer
    t0 = time.monotonic()
    request: dict[str, Any] = {
        "model": resolved,
        "messages": [{"role": "user", "content": _SCORE_PROMPT.format(doc=prefix)}],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if logprobs:
        request["logprobs"] = True
        request["top_logprobs"] = 1
    response = await client.chat.completions.create(**request)
    choice = response.choices[0]
    text = choice.message.content or ""
    lps: list[float] = []
    lp = getattr(choice, "logprobs", None)
    content = getattr(lp, "content", None) if lp is not None else None
    if content:
        lps = [c.logprob for c in content if c.logprob is not None]
    logger.info(
        "chat_score ok: model=%s took=%.1fs out_chars=%d logprob_tokens=%d",
        resolved,
        time.monotonic() - t0,
        len(text),
        len(lps),
    )
    return text, lps


async def embed(texts: list[str], *, model: str | None = None) -> list[list[float]]:
    """Batched embeddings via OpenRouter's /embeddings endpoint (verified
    live 2026-09; the models catalog doesn't tag embedding models, so the
    ID is trusted as config). One call per document — negligible cost."""
    client = _get_client()
    resolved = model or get_settings().models.embedder
    t0 = time.monotonic()
    response = await client.embeddings.create(model=resolved, input=texts)
    vectors = [d.embedding for d in response.data]
    logger.info(
        "embed ok: model=%s took=%.1fs n=%d dims=%d",
        resolved,
        time.monotonic() - t0,
        len(vectors),
        len(vectors[0]) if vectors else 0,
    )
    return vectors
