"""FastAPI server for live embedding inference via PPLXEmbedFP8Runtime."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Optional

import numpy as np
import torch
import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from modules.runtime import PPLXEmbedFP8Runtime
from modules.types import BatchStats

logger = logging.getLogger(__name__)


# --- Pydantic models ---

class DocumentInput(BaseModel):
    doc_id: str
    text: str


class EmbedRequest(BaseModel):
    documents: list[DocumentInput] = Field(..., min_length=1, max_length=1000)
    chunking: str = Field(
        default="semantic",
        pattern=r"^(semantic|sentences|paragraphs)$",
    )
    target_chunk_tokens: int = 768
    min_chunk_tokens: int = 256
    max_chunk_tokens: int = 1536
    similarity_percentile: float = 25.0


class DocumentResult(BaseModel):
    doc_id: str
    chunk_texts: list[str]
    embeddings: list[list[float]]


class StatsResponse(BaseModel):
    n_docs: int = 0
    n_chunks: int = 0
    n_tokens: int = 0
    n_batches: int = 0
    elapsed_s: float = 0.0
    oom_retries: int = 0
    tokens_per_sec: float = 0.0
    chunks_per_sec: float = 0.0
    docs_per_sec: float = 0.0


class EmbedResponse(BaseModel):
    results: list[DocumentResult]
    stats: StatsResponse


class HealthResponse(BaseModel):
    status: str
    fp8_enabled: bool
    device: str
    max_batch_tokens: int
    max_seq_len: int


# --- Sync helper ---

def _embed_sync(runtime: PPLXEmbedFP8Runtime, req: EmbedRequest) -> EmbedResponse:
    docs_input = [{"doc_id": d.doc_id, "text": d.text} for d in req.documents]
    try:
        results = runtime.embed_documents(
            documents=docs_input,
            chunking=req.chunking,
            show_progress=False,
            target_chunk_tokens=req.target_chunk_tokens,
            min_chunk_tokens=req.min_chunk_tokens,
            max_chunk_tokens=req.max_chunk_tokens,
            similarity_percentile=req.similarity_percentile,
        )
    except torch.cuda.OutOfMemoryError:
        raise HTTPException(status_code=507, detail="GPU out of memory")
    except Exception as e:
        logger.exception("embed_documents failed")
        raise HTTPException(status_code=500, detail=str(e))

    stats = runtime.last_stats
    doc_results = []
    for r in results:
        embeddings = r.embeddings.astype(np.float32).tolist()
        doc_results.append(
            DocumentResult(
                doc_id=r.doc_id,
                chunk_texts=r.chunk_texts,
                embeddings=embeddings,
            )
        )

    return EmbedResponse(
        results=doc_results,
        stats=StatsResponse(
            n_docs=stats.n_docs,
            n_chunks=stats.n_chunks,
            n_tokens=stats.n_tokens,
            n_batches=stats.n_batches,
            elapsed_s=stats.elapsed_s,
            oom_retries=stats.oom_retries,
            tokens_per_sec=stats.tokens_per_sec,
            chunks_per_sec=stats.chunks_per_sec,
            docs_per_sec=stats.docs_per_sec,
        ),
    )


def _stats_from_batch(stats: Optional[BatchStats]) -> StatsResponse:
    if stats is None:
        return StatsResponse()
    return StatsResponse(
        n_docs=stats.n_docs,
        n_chunks=stats.n_chunks,
        n_tokens=stats.n_tokens,
        n_batches=stats.n_batches,
        elapsed_s=stats.elapsed_s,
        oom_retries=stats.oom_retries,
        tokens_per_sec=stats.tokens_per_sec,
        chunks_per_sec=stats.chunks_per_sec,
        docs_per_sec=stats.docs_per_sec,
    )


# --- Lifespan ---

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Loading PPLXEmbedFP8Runtime…")
    runtime = PPLXEmbedFP8Runtime()
    app.state.runtime = runtime
    app.state.lock = asyncio.Lock()
    logger.info("Runtime ready")
    yield
    del app.state.runtime


app = FastAPI(title="Embeddings Server", lifespan=lifespan)


# --- Routes ---

@app.get("/health")
async def health():
    rt: PPLXEmbedFP8Runtime = app.state.runtime
    return HealthResponse(
        status="ok",
        fp8_enabled=rt.fp8_enabled,
        device=rt.device,
        max_batch_tokens=rt.max_batch_tokens,
        max_seq_len=rt.max_seq_len,
    )


@app.post("/embed", response_model=EmbedResponse)
async def embed(req: EmbedRequest):
    lock: asyncio.Lock = app.state.lock
    if lock.locked():
        raise HTTPException(status_code=503, detail="Server busy — GPU in use")

    async with lock:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None,
            _embed_sync,
            app.state.runtime,
            req,
        )


@app.get("/stats", response_model=StatsResponse)
async def stats():
    rt: PPLXEmbedFP8Runtime = app.state.runtime
    return _stats_from_batch(rt.last_stats)


# --- Entry point ---

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    uvicorn.run("server:app", host="0.0.0.0", port=8000, workers=1)
