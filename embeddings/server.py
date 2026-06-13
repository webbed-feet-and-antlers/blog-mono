"""FastAPI server for live + bulk embedding inference via PPLXEmbedFP8Runtime."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Optional

import numpy as np
import torch
import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from modules.runtime import PPLXEmbedFP8Runtime
from modules.types import BatchStats, DocumentEmbeddings
from gpu_queue import GPUWorker, JobTracker, Priority, QueueItem

logger = logging.getLogger(__name__)


# --- Pydantic models ---


class DocumentInput(BaseModel):
    doc_id: str
    text: str


class EmbedRequest(BaseModel):
    documents: list[DocumentInput] = Field(..., min_length=1, max_length=100)
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


class BulkEmbedRequest(BaseModel):
    documents: list[DocumentInput] = Field(..., min_length=1, max_length=5000)
    chunking: str = Field(
        default="semantic",
        pattern=r"^(semantic|sentences|paragraphs)$",
    )
    target_chunk_tokens: int = 768
    min_chunk_tokens: int = 256
    max_chunk_tokens: int = 1536
    similarity_percentile: float = 25.0
    chunk_size: int = Field(default=50, ge=10, le=200)


class BulkSubmitResponse(BaseModel):
    job_id: str
    total_chunks: int
    status: str
    created_at: str


class BulkStatusResponse(BaseModel):
    job_id: str
    status: str
    total_chunks: int
    completed_chunks: int


class BulkResultResponse(BaseModel):
    job_id: str
    status: str
    total_chunks: int
    completed_chunks: int
    results: list[list[DocumentResult]]
    stats: StatsResponse


class QueueStatusResponse(BaseModel):
    queue_depth: int
    items_processed: int
    active_jobs: int


# --- Helpers ---


def _embed_params(req: EmbedRequest | BulkEmbedRequest) -> dict:
    return dict(
        chunking=req.chunking,
        target_chunk_tokens=req.target_chunk_tokens,
        min_chunk_tokens=req.min_chunk_tokens,
        max_chunk_tokens=req.max_chunk_tokens,
        similarity_percentile=req.similarity_percentile,
    )


def _to_doc_results(embeddings: list[DocumentEmbeddings]) -> list[DocumentResult]:
    results = []
    for r in embeddings:
        results.append(
            DocumentResult(
                doc_id=r.doc_id,
                chunk_texts=r.chunk_texts,
                embeddings=r.embeddings.astype(np.float32).tolist(),
            )
        )
    return results


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

    queue: asyncio.PriorityQueue[QueueItem] = asyncio.PriorityQueue()
    tracker = JobTracker(max_completed_age_s=3600)
    worker = GPUWorker(runtime=runtime, queue=queue, job_tracker=tracker)
    await worker.start()

    app.state.gpu_worker = worker
    app.state.job_tracker = tracker
    app.state.queue = queue
    logger.info("Runtime and worker ready")
    yield
    await worker.stop()
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
    worker: GPUWorker = app.state.gpu_worker
    queue: asyncio.PriorityQueue[QueueItem] = app.state.queue
    loop = asyncio.get_running_loop()

    docs_input = [{"doc_id": d.doc_id, "text": d.text} for d in req.documents]
    future = loop.create_future()
    item = QueueItem(
        sort_key=(Priority.LIVE, worker.next_seq()),
        job_id="",
        chunk_index=-1,
        total_chunks=1,
        documents=docs_input,
        params=_embed_params(req),
        future=future,
    )
    await queue.put(item)

    try:
        results: list[DocumentEmbeddings] = await asyncio.wait_for(
            future, timeout=120
        )
    except asyncio.TimeoutError:
        future.cancel()
        raise HTTPException(status_code=408, detail="Request timed out")
    except torch.cuda.OutOfMemoryError:
        raise HTTPException(status_code=507, detail="GPU out of memory")
    except Exception as e:
        logger.exception("embed_documents failed")
        raise HTTPException(status_code=500, detail=str(e))

    doc_results = _to_doc_results(results)
    return EmbedResponse(
        results=doc_results,
        stats=_stats_from_batch(app.state.runtime.last_stats),
    )


@app.post("/embed/bulk", response_model=BulkSubmitResponse, status_code=202)
async def embed_bulk(req: BulkEmbedRequest):
    tracker: JobTracker = app.state.job_tracker
    worker: GPUWorker = app.state.gpu_worker
    queue: asyncio.PriorityQueue[QueueItem] = app.state.queue

    docs_input = [{"doc_id": d.doc_id, "text": d.text} for d in req.documents]
    chunks = [
        docs_input[i : i + req.chunk_size]
        for i in range(0, len(docs_input), req.chunk_size)
    ]
    params = _embed_params(req)
    job = tracker.create_job(total_chunks=len(chunks))

    for i, chunk in enumerate(chunks):
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        item = QueueItem(
            sort_key=(Priority.BULK_CHUNK, worker.next_seq()),
            job_id=job.job_id,
            chunk_index=i,
            total_chunks=len(chunks),
            documents=chunk,
            params=params,
            future=future,
        )
        await queue.put(item)

    return BulkSubmitResponse(
        job_id=job.job_id,
        total_chunks=job.total_chunks,
        status=job.status,
        created_at=datetime.fromtimestamp(
            job.created_at, tz=timezone.utc
        ).isoformat(),
    )


@app.get("/embed/bulk/{job_id}")
async def embed_bulk_status(job_id: str):
    tracker: JobTracker = app.state.job_tracker
    tracker.evict_old()

    job = tracker.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found or expired")

    if job.status in ("pending", "running"):
        return BulkStatusResponse(
            job_id=job.job_id,
            status=job.status,
            total_chunks=job.total_chunks,
            completed_chunks=job.completed_chunks,
        )

    if job.status == "failed":
        return BulkStatusResponse(
            job_id=job.job_id,
            status=job.status,
            total_chunks=job.total_chunks,
            completed_chunks=job.completed_chunks,
        )

    # completed
    all_results: list[list[DocumentResult]] = []
    for chunk_result in job.results:
        if chunk_result is not None:
            all_results.append(_to_doc_results(chunk_result))
        else:
            all_results.append([])

    return BulkResultResponse(
        job_id=job.job_id,
        status=job.status,
        total_chunks=job.total_chunks,
        completed_chunks=job.completed_chunks,
        results=all_results,
        stats=_stats_from_batch(app.state.runtime.last_stats),
    )


@app.get("/queue", response_model=QueueStatusResponse)
async def queue_status():
    worker: GPUWorker = app.state.gpu_worker
    queue: asyncio.PriorityQueue[QueueItem] = app.state.queue
    tracker: JobTracker = app.state.job_tracker
    return QueueStatusResponse(
        queue_depth=queue.qsize(),
        items_processed=worker.items_processed,
        active_jobs=sum(
            1 for j in tracker._jobs.values() if j.status in ("pending", "running")
        ),
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
