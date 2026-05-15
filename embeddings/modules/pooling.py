import torch
import torch.nn.functional as F

from .types import ChunkSpan


def quantize_int8_tanh(x: torch.Tensor) -> torch.Tensor:
    return torch.clamp(torch.round(torch.tanh(x) * 127), -128, 127).to(torch.int8)

def quantize_binary(x: torch.Tensor) -> torch.Tensor:
    return torch.where(x >= 0, torch.ones_like(x), -torch.ones_like(x))


def late_chunk_pool(hidden, attn_mask, spans_list, normalize=True, truncate_dim=None):
    device = hidden.device
    hdim = hidden.shape[-1]
    batch_size = hidden.shape[0]

    total_chunks = sum(len(s) if s else 0 for s in spans_list)
    if total_chunks == 0:
        return [torch.empty(0, hdim, device=device) for _ in spans_list]

    seq_len = hidden.shape[1]
    chunk_ids = torch.full((batch_size, seq_len), -1, dtype=torch.long, device=device)
    doc_chunk_offsets = []
    offset = 0
    for i, spans in enumerate(spans_list):
        doc_chunk_offsets.append(offset)
        if spans:
            for j, sp in enumerate(spans):
                chunk_ids[i, sp.start:sp.end] = offset + j
            offset += len(spans)

    valid = (chunk_ids >= 0) & (attn_mask.bool())
    chunk_ids = chunk_ids.where(valid, torch.zeros_like(chunk_ids))

    flat_hidden = hidden.reshape(-1, hdim)
    flat_mask = attn_mask.reshape(-1).float().unsqueeze(-1)
    flat_valid = valid.reshape(-1)
    flat_chunk_ids = chunk_ids.reshape(-1)
    masked_hidden = flat_hidden.float() * flat_mask
    idx = flat_chunk_ids.unsqueeze(-1).expand(-1, hdim)

    chunk_sums = torch.zeros(total_chunks, hdim, device=device, dtype=torch.float32)
    chunk_counts = torch.zeros(total_chunks, 1, device=device, dtype=torch.float32)
    valid_idx = idx[flat_valid]
    chunk_sums.scatter_add_(0, valid_idx, masked_hidden[flat_valid])
    chunk_counts.scatter_add_(0, flat_chunk_ids[flat_valid].unsqueeze(-1), flat_mask[flat_valid])

    chunk_embs = chunk_sums / chunk_counts.clamp(min=1e-9)
    if truncate_dim and truncate_dim < hdim:
        chunk_embs = chunk_embs[:, :truncate_dim]
    if normalize:
        chunk_embs = F.normalize(chunk_embs, p=2, dim=-1)

    results = []
    for i, spans in enumerate(spans_list):
        n = len(spans) if spans else 0
        edim = truncate_dim if (truncate_dim and truncate_dim < hdim) else hdim
        if n == 0:
            results.append(torch.empty(0, edim, device=device))
        else:
            results.append(chunk_embs[doc_chunk_offsets[i]:doc_chunk_offsets[i] + n])
    return results
