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


# ----- Stage 4: STS ---------------------------------------------------------

class STSScoreOut(BaseModel):
    """One STS pair score in [0.0, 5.0]."""

    pair_id: str
    score: float = Field(..., ge=0.0, le=5.0)


class STSBatchResponse(BaseModel):
    """Stage 4 batched response: 1+ STS scores."""

    scores: list[STSScoreOut] = Field(..., min_length=1)


# ----- Stage 5: Summary STS -------------------------------------------------
# Reuses STSScoreOut / STSBatchResponse (same 0-5 score shape).


# ----- Stage 6: Clustering --------------------------------------------------

class TopicAssignmentOut(BaseModel):
    """One chunk → topic assignment. Topic vocab is validated in code."""

    chunk_id: str
    topic: str = Field(..., min_length=1)


class TopicBatchResponse(BaseModel):
    """Stage 6 batched response: 1+ topic assignments."""

    assignments: list[TopicAssignmentOut] = Field(..., min_length=1)


# ----- Stage 7: Reranking ---------------------------------------------------

class RerankingScoreOut(BaseModel):
    """One candidate relevance score in [0, 3]."""

    chunk_id: str
    score: int = Field(..., ge=0, le=3)


class RerankingResponse(BaseModel):
    """Stage 7 response: per-query candidate scores."""

    query_id: str
    scores: list[RerankingScoreOut] = Field(..., min_length=1)


# ----- Stage 8: Cross-report retrieval --------------------------------------

class CrossReportMatchOut(BaseModel):
    """One candidate's binary relevance judgement."""

    chunk_id: str
    relevant: bool


class CrossReportResponse(BaseModel):
    """Stage 8 response: per-query binary relevance judgements."""

    query_id: str
    matches: list[CrossReportMatchOut] = Field(..., min_length=1)


# ----- Stage 9: Pair Classification -----------------------------------------

class PairClassifyOut(BaseModel):
    """One pair binary label (0 = different topic, 1 = same topic)."""

    pair_id: str
    label: int = Field(..., ge=0, le=1)


class PairClassifyBatchResponse(BaseModel):
    """Stage 9 batched response: 1+ binary labels."""

    items: list[PairClassifyOut] = Field(..., min_length=1)
