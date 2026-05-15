import time
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import torch


@dataclass
class ChunkSpan:
    start: int
    end: int
    text: str = ""

@dataclass
class Document:
    doc_id: str
    text: str
    chunks: list[ChunkSpan] = field(default_factory=list)
    chunk_texts: list[str] = field(default_factory=list)
    token_ids: Optional[torch.Tensor] = None
    attention_mask: Optional[torch.Tensor] = None
    n_tokens: int = 0

@dataclass
class DocumentEmbeddings:
    doc_id: str
    chunk_texts: list[str]
    embeddings: np.ndarray
    embeddings_int8: Optional[np.ndarray] = None
    embeddings_binary: Optional[np.ndarray] = None

@dataclass
class BatchStats:
    n_docs: int = 0
    n_chunks: int = 0
    n_tokens: int = 0
    n_batches: int = 0
    elapsed_s: float = 0.0
    oom_retries: int = 0
    _start_time: float = field(default_factory=time.time, repr=False)

    @property
    def _wall(self):
        return max(time.time() - self._start_time, 1e-9)

    @property
    def tokens_per_sec(self):
        return self.n_tokens / (self.elapsed_s or self._wall)

    @property
    def chunks_per_sec(self):
        return self.n_chunks / (self.elapsed_s or self._wall)

    @property
    def docs_per_sec(self):
        return self.n_docs / (self.elapsed_s or self._wall)

    def __repr__(self):
        return (
            f"BatchStats({self.n_docs} docs, {self.n_chunks} chunks, "
            f"{self.n_tokens:,} tok in {self.elapsed_s:.1f}s | "
            f"{self.tokens_per_sec:,.0f} tok/s, {self.chunks_per_sec:,.0f} ch/s | "
            f"{self.n_batches} batches, {self.oom_retries} OOMs)"
        )
