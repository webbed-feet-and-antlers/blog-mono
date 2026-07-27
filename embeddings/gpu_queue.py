"""Priority queue and GPU worker for embedding request processing."""

from __future__ import annotations

import asyncio
import itertools
import logging
import secrets
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Optional

from modules.runtime import PPLXEmbedFP8Runtime
from modules.types import DocumentEmbeddings

logger = logging.getLogger(__name__)


class Priority(IntEnum):
    LIVE = 0
    BULK_CHUNK = 1


@dataclass(order=True)
class QueueItem:
    sort_key: tuple[int, int]  # (priority, sequence_number)
    job_id: str = field(compare=False)
    chunk_index: int = field(compare=False)  # -1 for live requests
    total_chunks: int = field(compare=False)
    documents: list[dict] = field(compare=False)
    params: dict = field(compare=False)
    future: asyncio.Future = field(compare=False)


@dataclass
class BulkJob:
    job_id: str
    status: str  # pending | running | completed | failed
    total_chunks: int
    completed_chunks: int = 0
    results: list[Optional[list[DocumentEmbeddings]]] = field(default_factory=list)
    error_message: Optional[str] = None
    failed_chunk: Optional[int] = None
    created_at: float = field(default_factory=time.time)

    def __post_init__(self):
        if not self.results:
            self.results = [None] * self.total_chunks


class JobTracker:
    """In-memory store for bulk embedding jobs."""

    def __init__(self, max_completed_age_s: float = 3600):
        self._jobs: dict[str, BulkJob] = {}
        self.max_completed_age_s = max_completed_age_s

    def create_job(self, total_chunks: int) -> BulkJob:
        job_id = secrets.token_hex(16)
        job = BulkJob(
            job_id=job_id,
            status="pending",
            total_chunks=total_chunks,
        )
        self._jobs[job_id] = job
        return job

    def get_job(self, job_id: str) -> Optional[BulkJob]:
        return self._jobs.get(job_id)

    def update_chunk_result(
        self, job_id: str, chunk_index: int, result: list[DocumentEmbeddings]
    ) -> None:
        job = self._jobs.get(job_id)
        if job is None:
            return
        job.status = "running"
        job.results[chunk_index] = result
        job.completed_chunks += 1
        if job.completed_chunks >= job.total_chunks:
            job.status = "completed"

    def mark_chunk_failed(
        self, job_id: str, chunk_index: int, error_message: str
    ) -> None:
        job = self._jobs.get(job_id)
        if job is None:
            return
        job.status = "failed"
        job.error_message = error_message
        job.failed_chunk = chunk_index

    def evict_old(self) -> None:
        """Remove completed/failed jobs older than max_completed_age_s."""
        cutoff = time.time() - self.max_completed_age_s
        to_remove = [
            jid
            for jid, job in self._jobs.items()
            if job.status in ("completed", "failed") and job.created_at < cutoff
        ]
        for jid in to_remove:
            del self._jobs[jid]


class GPUWorker:
    """Single background worker that drains a priority queue on a dedicated thread."""

    def __init__(
        self,
        runtime: PPLXEmbedFP8Runtime,
        queue: asyncio.PriorityQueue[QueueItem],
        job_tracker: JobTracker,
    ):
        self._runtime = runtime
        self._queue = queue
        self._tracker = job_tracker
        self._executor = ThreadPoolExecutor(max_workers=1)
        self._task: Optional[asyncio.Task] = None
        self._seq = itertools.count()
        self._items_processed = 0

    @property
    def items_processed(self) -> int:
        return self._items_processed

    async def start(self) -> None:
        self._task = asyncio.create_task(self._loop())
        logger.info("GPU worker started")

    async def stop(self) -> None:
        # Drain remaining items and set exceptions on their futures
        while not self._queue.empty():
            try:
                item = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            if not item.future.done():
                item.future.set_exception(RuntimeError("Server shutting down"))
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._executor.shutdown(wait=False)
        logger.info("GPU worker stopped")

    def next_seq(self) -> int:
        return next(self._seq)

    async def _loop(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            try:
                item: QueueItem = await self._queue.get()
            except asyncio.CancelledError:
                logger.info("GPU worker loop cancelled")
                return

            # Skip if the future was already cancelled (e.g. timed-out live request)
            if item.future.cancelled():
                self._queue.task_done()
                continue

            try:
                result = await loop.run_in_executor(
                    self._executor, self._run_embed, item
                )
                if not item.future.cancelled():
                    item.future.set_result(result)
                    self._items_processed += 1

                    # Update bulk job tracker
                    if item.chunk_index >= 0:
                        self._tracker.update_chunk_result(
                            item.job_id, item.chunk_index, result
                        )
            except Exception as exc:
                if not item.future.cancelled():
                    item.future.set_exception(exc)

                # Mark bulk chunk as failed
                if item.chunk_index >= 0:
                    self._tracker.mark_chunk_failed(
                        item.job_id, item.chunk_index, str(exc)
                    )
                logger.exception(
                    "Error processing queue item (job=%s, chunk=%d): %s",
                    item.job_id,
                    item.chunk_index,
                    exc,
                )
            finally:
                self._queue.task_done()

    def _run_embed(self, item: QueueItem) -> list[DocumentEmbeddings]:
        """Blocking call executed on the ThreadPoolExecutor."""
        return self._runtime.embed_documents(
            documents=item.documents,
            show_progress=False,
            **item.params,
        )
