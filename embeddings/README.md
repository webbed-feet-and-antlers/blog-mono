Late-chunking runtime with NVIDIA Transformer Engine FP8
for pplx-embed-context-v1-0.6b.

Architecture:

- Bidirectional Qwen3 (0.6B params, ~0.6 GB in FP8)
- Chunks joined with SEP token; model sees full document context
- Late chunking via SEP-boundary detection in hidden states
- Mean pooling per chunk → optional tanh INT8 / binary quantization
- 1024-dim embeddings with MRL (Matryoshka) support

Strategy:

- HF flash_attention_2 handles varlen unpadding automatically —
  attention cost is Σ(Li²), not B×Lmax². No quadratic batch constraint
  needed; the linear token budget governs batch size.
- First-Fit Decreasing bin packer minimises padding waste for
  high-variance length distributions (e.g. lecture transcripts).
- te.Linear swap for FP8 matmul (Ada Lovelace sm_89+)
- GPU memory profiler determines optimal token budget at init.
- Sliding-window fallback for docs exceeding safe single-sequence length.
- Deferred D2H transfers (zero per-batch GPU stalls)
- torch.cuda.empty_cache() per batch to prevent allocator fragmentation;
  gc.collect() only on OOM recovery.

Target: NVIDIA L4 (24 GB, Ada Lovelace, sm_89)

Defaults tuned for lecture transcripts (p50=8K, p90=17K tokens):
chunking="semantic", target_chunk_tokens=400, min_chunk_tokens=128,
max_chunk_tokens=768, similarity_percentile=25.0

Requirements:
pip install "transformers>=4.52.0" accelerate torch numpy tqdm
pip install transformer-engine
pip install flash-attn --no-build-isolation

Usage:
runtime = PPLXEmbedFP8Runtime()
results = runtime.embed_documents(transcripts)
