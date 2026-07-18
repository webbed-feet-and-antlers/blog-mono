"""Pydantic schemas for DeepSeek JSON responses.

Each schema mirrors the JSON object the LLM is asked to emit; validation
failure triggers one repair call (see deepseek_client.py).
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ChunkOut(BaseModel):
    """One verbatim chunk emitted by Stage 1 (chunking)."""

    title: str = Field(..., min_length=3, max_length=200)
    text: str = Field(..., min_length=100)


class ChunkingResponse(BaseModel):
    """Stage 1 response: a non-empty list of chunks covering the report body."""

    chunks: list[ChunkOut] = Field(..., min_length=1)


class QueryOut(BaseModel):
    """One analyst-style retrieval query plus its gold answer."""

    query: str = Field(..., min_length=10, max_length=500)
    answer: str = Field(..., min_length=20)


class QueryGenerationResponse(BaseModel):
    """Stage 2 response: 1–3 diverse queries tied to a single chunk."""

    queries: list[QueryOut] = Field(..., min_length=1, max_length=3)
