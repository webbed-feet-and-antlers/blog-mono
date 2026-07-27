# Building a High-Throughput Embedding Server on a Single GPU

- The goal: serve embeddings from a 0.6B parameter model (pplx-embed-context-v1) on a single NVIDIA L4 (24 GB) for both real-time and bulk workloads.
- Late chunking: documents are tokenized as a whole with SEP tokens between chunks, so the model sees full document context before pooling. This produces better embeddings than chunking first and embedding independently.
- FP8 via NVIDIA Transformer Engine: all linear layers are swapped to TE's FP8 matmuls on Ada Lovelace (sm 89). The 0.6B model fits in ~0.6 GB in FP8, leaving most of the 24 GB for KV caches and batch tensors.
- Flash Attention 2 with variable-length unpadding: attention cost is the sum of squared sequence lengths, not batch size times max length squared. This means the limiting factor is total tokens in a batch, not the number of documents — so you can batch many short documents alongside a few long ones without quadratic blowup.
- First-Fit Decreasing bin packing: documents are sorted by length and packed into batches up to a token budget determined by a GPU memory profiler at init. This minimizes padding waste, which matters a lot for lecture transcripts where document lengths vary by 10x.
- Semantic chunking: instead of fixed-size chunks, sentence embeddings are computed first, then a vectorized boundary detection algorithm splits at points of low cosine similarity. The result is chunks that align with natural topic boundaries — tuned for lecture transcripts where a "chunk" is roughly 90 seconds of speech.
- Deferred GPU-to-CPU transfers: embedding tensors stay on GPU after pooling and are copied to CPU asynchronously via a pipeline. This eliminates per-batch stalls waiting for `.cpu()` calls.
- OOM recovery with recursive batch splitting: if a batch runs out of memory, it's split in half and retried. For single documents that still don't fit, a sliding window approach processes the sequence in overlapping segments and stitches the hidden states back together.
- Priority queue architecture for mixed workloads: live requests (`/embed`, max 100 docs) are enqueued at priority 0 and always processed before bulk chunks (`/embed/bulk`, max 5000 docs, priority 1). A single background worker drains the queue on a dedicated thread, so one bulk job can't starve interactive requests.
- Bulk jobs are split into chunks client-side, each submitted as a separate queue item. The server returns a job ID immediately (202), and clients poll for completion. This avoids holding HTTP connections open for minutes-long embedding runs.

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

Created embeddings/notebooks/test_queue_server.ipynb. Here's the workflow:

Before uploading to Colab, zip the project from the repo root:
zip -r embeddings.zip embeddings/ -x 'embeddings/.venv/_' 'embeddings/**pycache**/_' 'embeddings/uv.lock'

1. Cell 1 — installs all deps (fastapi, uvicorn, torch, transformers, flash-attn, transformer-engine, pyngrok, httpx)
2. Cell 2 — upload the zip, extracts it, cd into embeddings/
3. Cell 3 — set your ngrok auth token (free at https://dashboard.ngrok.com/get-started/your-authtoken)
4. Cell 4 — starts server.py in background, polls /health until ready
5. Cell 5 — opens ngrok tunnel for external access
6. Cells 6-8 — tests: live embed, bulk submit + poll, queue status, validation errors
7. Cell 9 — priority test: submits bulk, then immediately sends a live request that should jump the queue
8. Cell 10 — cleanup (kills server, closes tunnel, prints log)

You can skip the ngrok cells entirely and just test via localhost:8000 from within Colab — ngrok is only needed if you want to hit the server from your local machine.

1. componentise code
2. Notebook the code
3. Fast API code
4. batch code
5. queue code
6. Deploy it all - terraform to GCP fot batch + maybe runpod for cheap live code
7. Run it though with our data
8. Generate MTEB datasets with deepseek via openrouter (or some other good each batch processor)
9. Run perf benchmark
10. Run mteb quality benchmarks

MTEB

- ai generate
- conteb geography + covid datasets

data.mendeley.com/datasets/xknjp8pxbj/1
