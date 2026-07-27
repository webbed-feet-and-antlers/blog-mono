import gc
import logging

import torch

logger = logging.getLogger(__name__)


def _force_gc(device="cuda", threshold_mb=0):
    """
    Full GPU cleanup: empty_cache + gc.collect.

    threshold_mb > 0: only act when free GPU memory is below threshold.
    threshold_mb <= 0: always act (OOM recovery).
    """
    if device == "cuda" and torch.cuda.is_available():
        if threshold_mb > 0:
            free, _ = torch.cuda.mem_get_info()
            if free / 1e6 > threshold_mb:
                return
        torch.cuda.empty_cache()
    gc.collect()


def _free_mb() -> float:
    if torch.cuda.is_available():
        free, _ = torch.cuda.mem_get_info()
        return free / 1e6
    return float("inf")


def _cleanup_gpu():
    """Full GPU cleanup — use between benchmark runs, not in hot path."""
    torch.cuda.synchronize()
    torch.cuda.empty_cache()
    torch.cuda.ipc_collect()
    gc.collect()
    torch.cuda.synchronize()


class GPUMemoryProfiler:
    @staticmethod
    def profile(model_forward_fn, tokenizer, device,
                probe_seq_len=512, gpu_memory_utilization=0.85):
        """
        Estimate bytes-per-token from two probe forward passes,
        then derive max_batch_tokens from usable GPU memory.

        No quadratic attention budget is computed — flash_attention_2
        handles varlen unpadding internally so attention cost scales
        with Σ(Li²) per doc, not B×Lmax². The linear token budget
        alone governs batch sizing; OOM retry handles edge cases.
        """
        if device != "cuda":
            return {"max_batch_tokens": 4096, "bytes_per_token": 0, "free_bytes": 0}

        _force_gc(device)
        warmup = tokenizer("hello world " * 20, return_tensors="pt",
                           max_length=128, truncation=True)
        with torch.no_grad():
            _ = model_forward_fn(warmup["input_ids"].to(device),
                                 warmup["attention_mask"].to(device))
        del _, warmup
        _force_gc(device)
        torch.cuda.synchronize()
        baseline = torch.cuda.memory_allocated()
        logger.info(f"[profiler] Baseline: {baseline / 1e9:.2f} GB")

        def _probe(seq_len):
            words = "the quick brown fox " * (seq_len // 4 + 1)
            enc = tokenizer(words, return_tensors="pt",
                            max_length=seq_len, truncation=True)
            L = enc["input_ids"].shape[-1]
            torch.cuda.reset_peak_memory_stats()
            with torch.no_grad():
                _ = model_forward_fn(enc["input_ids"].to(device),
                                     enc["attention_mask"].to(device))
            torch.cuda.synchronize()
            peak = torch.cuda.max_memory_allocated() - baseline
            del _, enc
            _force_gc(device)
            torch.cuda.synchronize()
            return L, peak

        L1, M1 = _probe(probe_seq_len)
        L2, M2 = _probe(min(probe_seq_len * 2, 1024))

        bpt_simple = M1 / max(L1, 1)
        bpt_diff = (M2 - M1) / max(L2 - L1, 1) if L2 > L1 else bpt_simple
        bpt = min(bpt_simple, max(bpt_diff, bpt_simple * 0.3))

        free, total = torch.cuda.mem_get_info()
        usable = int(total * gpu_memory_utilization) - baseline
        max_batch_tokens = max(int(usable / bpt * 0.85), L1)

        logger.info(
            f"[profiler] {bpt / 1024:.1f} KB/tok → {max_batch_tokens:,} tok/batch "
            f"({gpu_memory_utilization * 100:.0f}% of {total / 1e9:.1f} GB)"
        )
        return {
            "max_batch_tokens": max_batch_tokens,
            "bytes_per_token": bpt,
            "free_bytes": usable,
            "baseline_bytes": baseline,
            "total_gpu_bytes": total,
        }
