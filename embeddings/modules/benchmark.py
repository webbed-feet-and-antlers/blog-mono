import time
import logging

import numpy as np
import torch
from tqdm import tqdm

from .types import BatchStats
from .gpu import _cleanup_gpu, _free_mb

logger = logging.getLogger(__name__)


def make_docs(n_docs, approx_tokens, tokenizer):
    words_per_doc = int(approx_tokens / 1.3)
    sentence = "The quick brown fox jumps over the lazy dog near the river bank. "
    n_sentences = max(1, words_per_doc // len(sentence.split()))
    docs = []
    for i in range(n_docs):
        text = f"Document {i} about topic {i % 50}. " + sentence * n_sentences
        docs.append({"doc_id": f"bench_{approx_tokens}_{i}", "text": text})
    enc = tokenizer(docs[0]["text"], truncation=True, max_length=32768)
    return docs, len(enc["input_ids"])


def _run_single_config(cfg_label, docs, runtime):
    _cleanup_gpu()
    free_gb = _free_mb() / 1024
    torch.cuda.reset_peak_memory_stats()
    t0 = time.time()
    runtime.embed_documents(docs, show_progress=False)
    torch.cuda.synchronize()
    elapsed = time.time() - t0
    stats   = runtime.get_stats()
    peak    = torch.cuda.max_memory_allocated() / 1e9
    return {
        "desc": cfg_label,
        "n_docs": stats.n_docs, "tokens": stats.n_tokens,
        "chunks": stats.n_chunks, "batches": stats.n_batches,
        "elapsed": elapsed, "tok_per_s": stats.tokens_per_sec,
        "chunk_per_s": stats.chunks_per_sec, "peak_gb": peak,
        "oom": stats.oom_retries, "free_gb_before": free_gb,
    }


def run_benchmark(runtime, tokenizer):
    budget    = runtime.max_batch_tokens
    gpu_name  = torch.cuda.get_device_name() if torch.cuda.is_available() else "CPU"
    mem_total = (torch.cuda.get_device_properties(0).total_memory / 1e9
                 if torch.cuda.is_available() else 0)

    print("=" * 80)
    print(f"BENCHMARK: pplx-embed-context-v1-0.6b on {gpu_name} ({mem_total:.0f} GB)")
    print(f"  dtype={runtime.dtype}, FP8={runtime.fp8_enabled}")
    print(f"  max_batch_tokens={budget:,}, max_seq_len={runtime.max_seq_len}")
    print(f"  padding_tolerance={runtime.padding_tolerance}")
    if runtime.profile_result:
        pr = runtime.profile_result
        print(f"  model={pr['baseline_bytes']/1e9:.2f} GB, "
              f"headroom={pr['free_bytes']/1e9:.1f} GB, "
              f"{pr['bytes_per_token']/1024:.1f} KB/tok")
    print("=" * 80)

    print("\nWarmup…")
    warmup_docs, _ = make_docs(10, 256, tokenizer)
    for _ in range(3):
        runtime.embed_documents(warmup_docs, show_progress=False)
    del warmup_docs
    _cleanup_gpu()

    standard_configs = [
        (None,  876,  "Transcript p10 (~876 tok)"),
        (None, 4059,  "Transcript p25 (~4K tok)"),
        (None, 8229,  "Transcript p50 (~8K tok)"),
        (None, 12367, "Transcript p75 (~12K tok)"),
        (None, 17108, "Transcript p90 (~17K tok)"),
        (None, 31340, "Transcript p99 (~31K tok)"),
        (1,    31340, "Single p99 transcript"),
        (100,  512,   "100 × 512 tok"),
        (50,   1024,  "50 × 1K tok"),
        (20,   2048,  "20 × 2K tok"),
    ]

    results = []

    for n_docs, approx_tok, desc in standard_configs:
        if n_docs is None:
            _, actual_tok = make_docs(1, approx_tok, tokenizer)
            # Use 85% of budget to ensure padding doesn't push us into 2 batches
            n_docs = max(1, int(budget * 0.85 / max(actual_tok, 1)))
        docs, actual_tok = make_docs(n_docs, approx_tok, tokenizer)
        print(f"\n{'─'*80}")
        print(f"{desc}: {n_docs} docs × ~{actual_tok} tok  (budget: {budget:,})")
        r = _run_single_config(desc, docs, runtime)
        print(f"  {r['batches']} batches, {r['oom']} OOM")
        print(f"  ⏱  {r['elapsed']:.2f}s | 📊 {r['tok_per_s']:,.0f} tok/s "
              f"| {r['chunk_per_s']:,.0f} ch/s")
        print(f"  GPU peak: {r['peak_gb']:.2f} GB")
        results.append(r)

    # Mixed transcript workload — runs last
    print(f"\n{'─'*80}")
    print("Mixed transcript workload (runs last — OOM cascade dirties allocator)")
    _cleanup_gpu()
    free_gb = _free_mb() / 1024
    print(f"  free before: {free_gb:.1f} GB")

    rng = np.random.default_rng(42)
    log_mu    = np.log(8229)
    log_sigma = (np.log(17108) - np.log(876)) / (2 * 1.282)
    token_counts = np.clip(
        rng.lognormal(log_mu, log_sigma, size=100).astype(int), 256, 32768
    )
    sentence = "The quick brown fox jumps over the lazy dog near the river bank. "
    mixed_docs = []
    for i, tc in enumerate(token_counts):
        n_sent = max(1, int(tc / 1.3) // len(sentence.split()))
        text = f"Lecture {i}, topic {i%20}. " + sentence * n_sent
        mixed_docs.append({"doc_id": f"transcript_{i}", "text": text})

    torch.cuda.reset_peak_memory_stats()
    t0 = time.time()
    runtime.embed_documents(mixed_docs, show_progress=False)
    torch.cuda.synchronize()
    elapsed = time.time() - t0
    stats   = runtime.get_stats()
    peak    = torch.cuda.max_memory_allocated() / 1e9

    print(f"  100 synthetic transcripts, {stats.n_tokens:,} tok")
    print(f"  {stats.n_batches} batches, {stats.oom_retries} OOM")
    print(f"  ⏱  {elapsed:.2f}s | 📊 {stats.tokens_per_sec:,.0f} tok/s "
          f"| {stats.chunks_per_sec:,.0f} ch/s")
    print(f"  GPU peak: {peak:.2f} GB")
    results.append({
        "desc": "Mixed transcripts (100)",
        "n_docs": 100, "tokens": stats.n_tokens,
        "chunks": stats.n_chunks, "batches": stats.n_batches,
        "elapsed": elapsed, "tok_per_s": stats.tokens_per_sec,
        "chunk_per_s": stats.chunks_per_sec, "peak_gb": peak,
        "oom": stats.oom_retries,
    })

    print(f"\n{'='*80}\nSUMMARY\n{'='*80}")
    print(f"{'Workload':<35} {'Docs':>5} {'Tokens':>10} {'Chunks':>7} "
          f"{'Batch':>6} {'OOM':>4} {'Time':>7} {'Tok/s':>10} {'Ch/s':>8} {'Peak':>6}")
    print("─" * 112)
    for r in results:
        print(f"  {r['desc']:<33} {r['n_docs']:>5} {r['tokens']:>10,} "
              f"{r['chunks']:>7} {r['batches']:>6} {r.get('oom',0):>4} {r['elapsed']:>6.2f}s "
              f"{r['tok_per_s']:>10,.0f} {r['chunk_per_s']:>8,.0f} "
              f"{r['peak_gb']:>5.1f}G")

    best = max(results, key=lambda r: r["tok_per_s"])
    print(f"\n🏆 Peak: {best['tok_per_s']:,.0f} tok/s on '{best['desc']}'")
    return results
