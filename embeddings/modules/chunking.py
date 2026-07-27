import re

import torch
import torch.nn.functional as F

from .types import ChunkSpan


class Chunker:
    @staticmethod
    def by_sentences(text: str) -> list[str]:
        return [p for p in re.split(r'(?<=[.!?])\s+', text.strip()) if p.strip()]

    @staticmethod
    def by_paragraphs(text: str) -> list[str]:
        return [p.strip() for p in re.split(r'\n\s*\n', text.strip()) if p.strip()]


def find_semantic_boundaries(
    sentence_embeddings: torch.Tensor,
    sentence_token_counts: list[int],
    target_chunk_tokens: int = 400,
    min_chunk_tokens: int = 128,
    max_chunk_tokens: int = 768,
    similarity_percentile: float = 25.0,
) -> list[int]:
    """
    Find optimal semantic chunk boundaries from sentence embeddings.

    Defaults tuned for lecture transcripts:
      target=400, min=128, max=768,
      similarity_percentile=25 (lectures are topic-continuous;
      fewer splits than the default 30 for prose).
    """
    n = sentence_embeddings.shape[0]
    if n <= 1:
        return [0]

    sims = F.cosine_similarity(
        sentence_embeddings[:-1], sentence_embeddings[1:], dim=-1,
    )
    threshold = torch.quantile(sims, similarity_percentile / 100.0).item()

    split_scores = [(i + 1, sims[i].item()) for i in range(len(sims))]
    split_scores.sort(key=lambda x: x[1])

    token_counts = sentence_token_counts
    boundaries = {0}

    chunk_start = 0
    chunk_tokens = 0
    for i in range(n):
        chunk_tokens += token_counts[i]
        if chunk_tokens > max_chunk_tokens and i > chunk_start:
            best_pos = None
            for pos, sim in split_scores:
                if chunk_start < pos <= i:
                    left_tokens = sum(token_counts[chunk_start:pos])
                    if left_tokens >= min_chunk_tokens:
                        best_pos = pos
                        break
            if best_pos is not None:
                boundaries.add(best_pos)
                chunk_start = best_pos
                chunk_tokens = sum(token_counts[best_pos:i + 1])
            else:
                boundaries.add(i)
                chunk_start = i
                chunk_tokens = token_counts[i]

    for pos, sim in split_scores:
        if sim > threshold:
            break
        sorted_bounds = sorted(boundaries)
        chunk_idx = 0
        for j, b in enumerate(sorted_bounds):
            if b <= pos:
                chunk_idx = j
            else:
                break
        chunk_start_sent = sorted_bounds[chunk_idx]
        chunk_end_sent = (
            sorted_bounds[chunk_idx + 1] if chunk_idx + 1 < len(sorted_bounds) else n
        )
        left_tokens = sum(token_counts[chunk_start_sent:pos])
        right_tokens = sum(token_counts[pos:chunk_end_sent])
        total_chunk = left_tokens + right_tokens
        if (
            left_tokens >= min_chunk_tokens
            and right_tokens >= min_chunk_tokens
            and total_chunk > target_chunk_tokens
            and left_tokens >= target_chunk_tokens * 0.5 # prevent undersized left chunk
            and right_tokens >= target_chunk_tokens * 0.5 # prevent undersized right chunk
        ):
            boundaries.add(pos)

    return sorted(boundaries)
