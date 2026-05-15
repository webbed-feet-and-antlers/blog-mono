from typing import Optional

import torch

from .types import Document


class TranscriptBatcher:
    """
    First-Fit Decreasing bin packer with token budget constraint.

    The only memory constraint is the linear token budget (max_batch_tokens),
    which governs total padded tokens per batch. Flash attention's varlen
    kernel handles attention in O(Σ Li²) not O(B × Lmax²), so no separate
    quadratic attention budget is needed.

    padding_tolerance: acceptable padding overhead (1.4 = up to 40% waste).
    max_docs_per_batch: hard cap on documents per batch (optional).
    """

    def __init__(
        self,
        max_batch_tokens: int,
        max_seq_len: int,
        padding_tolerance: float = 1.4,
        max_docs_per_batch: Optional[int] = None,
    ):
        self.max_batch_tokens = max_batch_tokens
        self.max_seq_len = max_seq_len
        self.padding_tolerance = padding_tolerance
        self.max_docs_per_batch = max_docs_per_batch

    def create_batches(self, docs: list) -> list[list[int]]:
        order = sorted(range(len(docs)), key=lambda i: docs[i].n_tokens, reverse=True)

        bin_indices: list[list[int]] = []
        bin_max:     list[int]       = []
        bin_actual:  list[int]       = []

        for idx in order:
            n = docs[idx].n_tokens
            if n > self.max_seq_len:
                bin_indices.append([idx])
                bin_max.append(n)
                bin_actual.append(n)
                continue

            placed = False
            for k in range(len(bin_indices)):
                if (self.max_docs_per_batch is not None
                        and len(bin_indices[k]) >= self.max_docs_per_batch):
                    continue
                new_max = max(bin_max[k], n)
                padded  = new_max * (len(bin_indices[k]) + 1)
                actual  = bin_actual[k] + n
                if (
                    padded <= self.max_batch_tokens
                    and padded <= actual * self.padding_tolerance
                ):
                    bin_indices[k].append(idx)
                    bin_max[k]    = new_max
                    bin_actual[k] = actual
                    placed = True
                    break

            if not placed:
                bin_indices.append([idx])
                bin_max.append(n)
                bin_actual.append(n)

        return bin_indices

    def split_batch(self, indices, docs=None):
        """Split a batch for OOM recovery."""
        if len(indices) <= 1:
            return [indices]
        if docs is not None:
            largest = max(indices, key=lambda i: docs[i].n_tokens)
            rest = [i for i in indices if i != largest]
            return [[largest], rest] if rest else [[largest]]
        mid = len(indices) // 2
        return [indices[:mid], indices[mid:]]


def collate_batch(docs, indices, pin_memory=True):
    """Pre-allocate + narrow-copy to avoid CatArrayBatchedCopy dispatches."""
    batch = [docs[i] for i in indices]
    ml = max(d.n_tokens for d in batch)
    n = len(batch)
    spans_l = [d.chunks for d in batch]

    ids_out  = torch.zeros(n, ml, dtype=torch.long)
    mask_out = torch.zeros(n, ml, dtype=torch.long)

    for i, d in enumerate(batch):
        L = d.n_tokens
        ids_out[i, :L].copy_(d.token_ids.squeeze(0))
        mask_out[i, :L].copy_(d.attention_mask.squeeze(0))

    if pin_memory and torch.cuda.is_available():
        ids_out  = ids_out.pin_memory()
        mask_out = mask_out.pin_memory()
    return ids_out, mask_out, spans_l
