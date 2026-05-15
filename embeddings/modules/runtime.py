import gc
import time
import logging
from typing import Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
import torch
import torch.nn.functional as F
from transformers import AutoModel, AutoTokenizer
from tqdm import tqdm

from .constants import MODEL_ID
from .types import Document, DocumentEmbeddings, BatchStats
from .chunking import Chunker, find_semantic_boundaries
from .tokenization import tokenize_doc_with_sep
from .pooling import late_chunk_pool, quantize_int8_tanh, quantize_binary
from .gpu import _force_gc, GPUMemoryProfiler
from .batcher import TranscriptBatcher, collate_batch
from .d2h import DeferredD2HPipeline
from .fp8 import _check_te_available, _check_fp8_support, swap_linear_to_te, swap_rmsnorm_to_fused

logger = logging.getLogger(__name__)


class PPLXEmbedFP8Runtime:
    """
    High-throughput late-chunking runtime for pplx-embed-context-v1-0.6b
    with FP8 Tensor Cores.

    Tuned for lecture transcript workloads:
      - TranscriptBatcher (FFD) minimises padding waste for 10× length variance
      - Semantic chunking defaults calibrated to ~90s speech segments
      - empty_cache() per batch prevents allocator fragmentation on mixed
        workloads; gc.collect() only on OOM recovery
    """

    def __init__(
        self,
        device: str = "cuda",
        max_seq_len: int = 32768,
        max_batch_tokens: Optional[int] = None,
        gpu_memory_utilization: float = 0.90,
        truncate_dim: Optional[int] = None,
        fp8_enabled: Optional[bool] = None,
        output_int8: bool = False,
        output_binary: bool = False,
        padding_tolerance: float = 1.4,
        max_docs_per_batch: Optional[int] = None,
    ):
        self.device = device
        self.max_seq_len = max_seq_len
        self.truncate_dim = truncate_dim
        self.output_int8 = output_int8
        self.output_binary = output_binary
        self.dtype = torch.float16
        self.padding_tolerance = padding_tolerance

        if fp8_enabled is None:
            self.fp8_enabled = _check_te_available() and _check_fp8_support()
        else:
            self.fp8_enabled = fp8_enabled

        attn_impl = self._detect_best_attn(device)

        logger.info(f"Loading {MODEL_ID} (attn={attn_impl})")
        self.tokenizer = AutoTokenizer.from_pretrained(MODEL_ID, trust_remote_code=True)

        kw = dict(trust_remote_code=True, torch_dtype=torch.float16)
        if attn_impl:
            kw["attn_implementation"] = attn_impl

        self.model = AutoModel.from_pretrained(MODEL_ID, **kw)
        self.model = self.model.to(device).eval()

        if device == "cuda":
            logger.info(
                f"Model loaded (fp16): {torch.cuda.memory_allocated() / 1e9:.2f} GB"
            )

        self.te_module = None
        self.te_recipe = None
        if self.fp8_enabled:
            try:
                import transformer_engine.pytorch as te
                from transformer_engine.common import recipe
                backbone = self.model.model if hasattr(self.model, "model") else self.model
                n_swapped = swap_linear_to_te(backbone)
                if n_swapped > 0:
                    self.te_recipe = recipe.DelayedScaling(
                        fp8_format=recipe.Format.E4M3,
                        amax_history_len=16,
                        amax_compute_algo="max",
                    )
                    self.te_module = te
                    n_norms = swap_rmsnorm_to_fused(backbone)
                    logger.info(
                        f"FP8 active: {n_swapped} linears, {n_norms} norms fused, "
                        f"{torch.cuda.memory_allocated() / 1e9:.2f} GB"
                    )
                else:
                    logger.warning("No layers swapped — FP8 disabled")
                    self.fp8_enabled = False
            except ImportError:
                logger.warning("TE not importable — FP8 disabled")
                self.fp8_enabled = False

        if max_batch_tokens is not None:
            self._max_batch_tokens = max_batch_tokens
            self.profile_result = None
        else:
            logger.info("Profiling GPU memory…")
            self.profile_result = GPUMemoryProfiler.profile(
                model_forward_fn=self._forward_for_profile,
                tokenizer=self.tokenizer,
                device=device,
                gpu_memory_utilization=gpu_memory_utilization,
            )
            self._max_batch_tokens = self.profile_result["max_batch_tokens"]

        self._bytes_per_token: float = (
            self.profile_result["bytes_per_token"]
            if self.profile_result and self.profile_result.get("bytes_per_token", 0) > 0
            else 0.0
        )

        if device == "cuda" and torch.cuda.is_available():
            _, self._total_gpu_bytes = torch.cuda.mem_get_info()
        else:
            self._total_gpu_bytes = 0

        self.batcher = TranscriptBatcher(
            self._max_batch_tokens, max_seq_len,
            padding_tolerance, max_docs_per_batch,
        )
        self._d2h = DeferredD2HPipeline(device=device)
        self._d2h_overflow: dict = {}
        self.last_stats: Optional[BatchStats] = None

        mode = "FP8 (TE)" if self.fp8_enabled else "FP16"
        logger.info(
            f"Ready: {mode}, {self._max_batch_tokens:,} tok/batch, "
            f"padding_tolerance={padding_tolerance}"
        )

    @property
    def max_batch_tokens(self) -> int:
        return self._max_batch_tokens

    @staticmethod
    def _detect_best_attn(device):
        if device != "cuda":
            return "eager"
        try:
            import flash_attn
            logger.info(f"flash-attn {flash_attn.__version__} → flash_attention_2")
            return "flash_attention_2"
        except ImportError:
            logger.info("No flash-attn → sdpa")
            return "sdpa"

    @staticmethod
    def _pad_to_fp8_alignment(input_ids, attention_mask):
        rem = input_ids.shape[1] % 8
        if rem == 0:
            return input_ids, attention_mask, input_ids.shape[1]
        pad = 8 - rem
        return (
            F.pad(input_ids, (0, pad), value=0),
            F.pad(attention_mask, (0, pad), value=0),
            input_ids.shape[1],
        )

    def _get_hidden_states(self, input_ids, attention_mask):
        orig_seq_len = None
        if self.fp8_enabled:
            input_ids, attention_mask, orig_seq_len = (
                self._pad_to_fp8_alignment(input_ids, attention_mask)
            )

        if self.fp8_enabled and self.te_module is not None:
            ctx = self.te_module.fp8_autocast(enabled=True, fp8_recipe=self.te_recipe)
        else:
            ctx = torch.cuda.amp.autocast(dtype=torch.float16)

        with ctx:
            outputs = self.model(
                input_ids=input_ids,
                attention_mask=attention_mask,
                output_hidden_states=True,
            )
            hidden = (
                outputs.last_hidden_state
                if hasattr(outputs, "last_hidden_state")
                else outputs[0]
            )

        if orig_seq_len is not None and hidden.shape[1] > orig_seq_len:
            hidden = hidden[:, :orig_seq_len, :]

        return hidden

    @torch.no_grad()
    def _forward_for_profile(self, input_ids, attention_mask):
        return self._get_hidden_states(input_ids, attention_mask)

    # --- Live free-memory estimate ---

    def _estimate_max_single_seq(self) -> int:
        if self.device == "cuda" and torch.cuda.is_available() and self._bytes_per_token > 0:
            free, total = torch.cuda.mem_get_info()
            live_safe  = int(free  * 0.30 / self._bytes_per_token)
            floor_safe = int(total * 0.10 / self._bytes_per_token)
            safe = max(live_safe, floor_safe)
            return max(min(safe, self.max_seq_len), 512)
        return min(4096, self.max_seq_len)

    # --- Sliding window fallback ---

    @torch.no_grad()
    def _forward_single_windowed(self, doc, window_size=8192, overlap=1024):
        ids = doc.token_ids.squeeze(0)
        mask = doc.attention_mask.squeeze(0)
        seq_len = ids.shape[0]
        step = window_size - overlap
        hidden_parts = []
        pos = 0

        while pos < seq_len:
            end = min(pos + window_size, seq_len)
            w_ids  = ids[pos:end].unsqueeze(0).to(self.device)
            w_mask = mask[pos:end].unsqueeze(0).to(self.device)
            w_hidden = self._get_hidden_states(w_ids, w_mask)

            if pos == 0:
                hidden_parts.append(w_hidden[0])
            else:
                skip = min(overlap, w_hidden.shape[1])
                hidden_parts.append(w_hidden[0, skip:])

            del w_ids, w_mask, w_hidden
            if end >= seq_len:
                break
            pos += step

        full_hidden = torch.cat(hidden_parts, dim=0)
        del hidden_parts

        if full_hidden.shape[0] > seq_len:
            full_hidden = full_hidden[:seq_len]
        elif full_hidden.shape[0] < seq_len:
            pad_len = seq_len - full_hidden.shape[0]
            full_hidden = torch.cat([
                full_hidden,
                torch.zeros(pad_len, full_hidden.shape[1],
                            device=full_hidden.device, dtype=full_hidden.dtype),
            ], dim=0)

        return full_hidden.unsqueeze(0)

    def _process_single_windowed_safe(self, doc, max_window, stats):
        live_max = self._estimate_max_single_seq()
        effective_max = min(max_window, live_max)

        fractions = [1.0, 0.75, 0.50, 0.25, 0.125]
        window_sizes = sorted(
            set(max(int(effective_max * f), 512) for f in fractions),
            reverse=True,
        )

        for ws in window_sizes:
            overlap = min(1024, ws // 4)
            if self.device == "cuda":
                torch.cuda.synchronize()
                torch.cuda.empty_cache()
                torch.cuda.ipc_collect()
            gc.collect()
            try:
                hidden = self._forward_single_windowed(doc, ws, overlap)
                mask_gpu = doc.attention_mask.to(self.device)
                chunk_embs = late_chunk_pool(
                    hidden, mask_gpu, [doc.chunks],
                    normalize=True, truncate_dim=self.truncate_dim,
                )
                del hidden, mask_gpu
                fallback = self._submit_results([doc], [0], chunk_embs, stats)
                stats.n_batches += 1
                del chunk_embs
                return fallback

            except torch.cuda.OutOfMemoryError:
                if self.device == "cuda":
                    torch.cuda.synchronize()
                    torch.cuda.empty_cache()
                    torch.cuda.ipc_collect()
                gc.collect()
                continue

        logger.error(
            f"All windows failed for '{doc.doc_id}' "
            f"(seq_len={doc.n_tokens}, est_max={live_max}). Skipping."
        )
        dim = self.truncate_dim or 1024
        return {
            doc.doc_id: DocumentEmbeddings(
                doc.doc_id,
                doc.chunk_texts,
                np.zeros((max(len(doc.chunks), 1), dim), dtype=np.float32),
            )
        }

    # --- Document preparation ---

    def _prepare_single(self, d, chunking):
        fn = Chunker.by_paragraphs if chunking == "paragraphs" else Chunker.by_sentences
        chunk_texts = fn(d["text"]) or [d["text"]]
        enc, spans = tokenize_doc_with_sep(chunk_texts, self.tokenizer, self.max_seq_len)
        return Document(
            doc_id=d["doc_id"],
            text=d["text"],
            chunks=spans,
            chunk_texts=chunk_texts,
            token_ids=enc["input_ids"],
            attention_mask=enc["attention_mask"],
            n_tokens=enc["input_ids"].shape[-1],
        )

    def _prepare_documents(self, documents, chunking):
        n = len(documents)
        if n <= 4:
            return [self._prepare_single(d, chunking) for d in documents]
        workers = min(8, n)
        prepared = [None] * n
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futs = {pool.submit(self._prepare_single, d, chunking): i
                    for i, d in enumerate(documents)}
            for f in as_completed(futs):
                prepared[futs[f]] = f.result()
        return prepared

    # --- Semantic chunking — vectorised two-stage pooling ---

    def _semantic_chunk_and_pool(
        self, hidden, attn_mask, spans_list, sentence_texts_list,
        target_chunk_tokens=768, min_chunk_tokens=256,
        max_chunk_tokens=1536, similarity_percentile=25.0,
    ):
        device = hidden.device
        hdim = hidden.shape[-1]
        batch_size = hidden.shape[0]
        seq_len = hidden.shape[1]

        # Stage 1: vectorised sentence pooling
        total_sents = 0
        doc_sent_offsets = []
        for spans in spans_list:
            doc_sent_offsets.append(total_sents)
            total_sents += len(spans) if spans else 0

        if total_sents == 0:
            edim = self.truncate_dim if self.truncate_dim and self.truncate_dim < hdim else hdim
            return ([torch.empty(0, edim, device=device) for _ in spans_list],
                    [[] for _ in spans_list])

        sent_ids = torch.full((batch_size, seq_len), -1, dtype=torch.long, device=device)
        sent_token_counts_by_doc: dict[int, list[int]] = {}

        for i, spans in enumerate(spans_list):
            if not spans:
                sent_token_counts_by_doc[i] = []
                continue
            offset = doc_sent_offsets[i]
            counts = []
            for j, sp in enumerate(spans):
                sent_ids[i, sp.start:sp.end] = offset + j
                counts.append(sp.end - sp.start)
            sent_token_counts_by_doc[i] = counts

        valid = (sent_ids >= 0) & (attn_mask.bool())
        sent_ids_safe = sent_ids.where(valid, torch.zeros_like(sent_ids))

        flat_hidden = hidden.reshape(-1, hdim).float()
        flat_mask_1d = attn_mask.reshape(-1).float()            # (-1,)
        flat_mask_2d = flat_mask_1d.unsqueeze(-1)                # (-1, 1)
        flat_valid = valid.reshape(-1)
        flat_sent_ids = sent_ids_safe.reshape(-1)
        masked_hidden = flat_hidden * flat_mask_2d  # reused in Stage 3

        idx = flat_sent_ids.unsqueeze(-1).expand(-1, hdim)
        sent_sums   = torch.zeros(total_sents, hdim, device=device, dtype=torch.float32)
        sent_counts = torch.zeros(total_sents, 1,    device=device, dtype=torch.float32)
        valid_idx = idx[flat_valid]
        sent_sums.scatter_add_(0, valid_idx, masked_hidden[flat_valid])
        sent_counts.scatter_add_(0, flat_sent_ids[flat_valid].unsqueeze(-1),
                                 flat_mask_2d[flat_valid])

        sent_embs_all  = sent_sums / sent_counts.clamp(min=1e-9)
        sent_embs_norm = F.normalize(sent_embs_all, p=2, dim=-1)

        # Stage 2: per-doc boundary detection (CPU, small tensors)
        all_merged_spans = []
        all_chunk_texts  = []

        for i, spans in enumerate(spans_list):
            sent_texts = sentence_texts_list[i] if i < len(sentence_texts_list) else []
            if not spans:
                all_merged_spans.append([])
                all_chunk_texts.append([])
                continue

            n_sents = len(spans)
            offset  = doc_sent_offsets[i]
            doc_sent_embs       = sent_embs_norm[offset:offset + n_sents]
            doc_sent_tok_counts = sent_token_counts_by_doc[i]

            boundaries = find_semantic_boundaries(
                doc_sent_embs, doc_sent_tok_counts,
                target_chunk_tokens=target_chunk_tokens,
                min_chunk_tokens=min_chunk_tokens,
                max_chunk_tokens=max_chunk_tokens,
                similarity_percentile=similarity_percentile,
            )

            merged_spans, chunk_texts = [], []
            for ci in range(len(boundaries)):
                start_sent = boundaries[ci]
                end_sent   = boundaries[ci + 1] if ci + 1 < len(boundaries) else n_sents
                tok_start  = spans[start_sent].start
                tok_end    = spans[end_sent - 1].end
                merged_spans.append((tok_start, tok_end))
                merged = " ".join(
                    sent_texts[j] for j in range(start_sent, end_sent)
                    if j < len(sent_texts)
                )
                chunk_texts.append(merged)

            all_merged_spans.append(merged_spans)
            all_chunk_texts.append(chunk_texts)

        # Stage 3: vectorised re-pooling over merged spans (reuses masked_hidden)
        total_chunks = sum(len(ms) for ms in all_merged_spans)
        if total_chunks == 0:
            edim = self.truncate_dim if self.truncate_dim and self.truncate_dim < hdim else hdim
            return ([torch.empty(0, edim, device=device) for _ in spans_list],
                    [[] for _ in spans_list])

        chunk_ids = torch.full((batch_size, seq_len), -1, dtype=torch.long, device=device)
        doc_chunk_offsets = []
        chunk_offset = 0

        for i, merged_spans in enumerate(all_merged_spans):
            doc_chunk_offsets.append(chunk_offset)
            for j, (ts, te) in enumerate(merged_spans):
                chunk_ids[i, ts:te] = chunk_offset + j
            chunk_offset += len(merged_spans)

        valid_c        = (chunk_ids >= 0) & (attn_mask.bool())
        chunk_ids_safe = chunk_ids.where(valid_c, torch.zeros_like(chunk_ids))

        flat_valid_c   = valid_c.reshape(-1)
        flat_chunk_ids = chunk_ids_safe.reshape(-1)
        c_idx          = flat_chunk_ids.unsqueeze(-1).expand(-1, hdim)

        chunk_sums   = torch.zeros(total_chunks, hdim, device=device, dtype=torch.float32)
        chunk_counts = torch.zeros(total_chunks, 1,    device=device, dtype=torch.float32)
        valid_c_idx  = c_idx[flat_valid_c]
        chunk_sums.scatter_add_(0, valid_c_idx, masked_hidden[flat_valid_c])
        chunk_counts.scatter_add_(0, flat_chunk_ids[flat_valid_c].unsqueeze(-1),
                                  flat_mask_2d[flat_valid_c])

        chunk_embs = chunk_sums / chunk_counts.clamp(min=1e-9)
        if self.truncate_dim and self.truncate_dim < hdim:
            chunk_embs = chunk_embs[:, :self.truncate_dim]
        chunk_embs = F.normalize(chunk_embs, p=2, dim=-1)

        all_chunk_embs = []
        for i, merged_spans in enumerate(all_merged_spans):
            n = len(merged_spans)
            if n == 0:
                edim = self.truncate_dim if self.truncate_dim and self.truncate_dim < hdim else hdim
                all_chunk_embs.append(torch.empty(0, edim, device=device))
            else:
                start = doc_chunk_offsets[i]
                all_chunk_embs.append(chunk_embs[start:start + n])

        return all_chunk_embs, all_chunk_texts

    # --- Submit results ---

    def _submit_results(self, docs, indices, chunk_embs_list, stats,
                        override_texts=None):
        fallback = {}
        for j, di in enumerate(indices):
            doc     = docs[di]
            emb_gpu = chunk_embs_list[j]
            texts   = (override_texts[j] if (override_texts and j in override_texts)
                       else doc.chunk_texts[:len(doc.chunks)])
            int8_gpu = quantize_int8_tanh(emb_gpu) if self.output_int8 else None
            bin_gpu  = quantize_binary(emb_gpu)     if self.output_binary else None
            stats.n_chunks += len(texts)
            stats.n_tokens += doc.n_tokens
            try:
                self._d2h.submit(doc.doc_id, texts, emb_gpu, int8_gpu, bin_gpu)
            except Exception as e:
                logger.warning(f"Deferred D2H failed for {doc.doc_id}: {e}")
                fallback[doc.doc_id] = DocumentEmbeddings(
                    doc.doc_id, texts,
                    emb_gpu.cpu().float().numpy(),
                    int8_gpu.cpu().numpy() if int8_gpu is not None else None,
                    bin_gpu.cpu().numpy()  if bin_gpu  is not None else None,
                )
        return fallback

    # --- Batch processing with OOM recovery ---

    @torch.no_grad()
    def _process_batch(self, docs, indices, stats, _depth=0, semantic_kwargs=None):
        if len(indices) == 1:
            doc = docs[indices[0]]
            max_single = self._estimate_max_single_seq()
            if doc.n_tokens > max_single:
                return self._process_single_windowed_safe(doc, max_single, stats)

        ids, mask, spans = collate_batch(docs, indices)
        try:
            hidden   = self._get_hidden_states(ids.to(self.device, non_blocking=True),
                                               mask.to(self.device, non_blocking=True))
            mask_gpu = mask.to(self.device)
            del ids, mask

            if semantic_kwargs is not None:
                sentence_texts_list = [docs[di].chunk_texts for di in indices]
                chunk_embs_list, chunk_texts_list = self._semantic_chunk_and_pool(
                    hidden, mask_gpu, spans, sentence_texts_list, **semantic_kwargs,
                )
                del hidden, mask_gpu
                override_texts = {j: t for j, t in enumerate(chunk_texts_list)}
                fallback = self._submit_results(docs, indices, chunk_embs_list, stats,
                                                override_texts=override_texts)
            else:
                chunk_embs = late_chunk_pool(hidden, mask_gpu, spans,
                                             normalize=True, truncate_dim=self.truncate_dim)
                del hidden, mask_gpu
                fallback = self._submit_results(docs, indices, chunk_embs, stats)
                del chunk_embs

            stats.n_batches += 1
            del spans
            # Return cached allocator blocks to the driver between batches.
            # Prevents fragmentation-driven OOMs on mixed-length workloads
            # where successive batches have very different tensor shapes.
            # empty_cache() is ~1-5ms; gc.collect() (~500ms) is skipped.
            if self.device == "cuda":
                torch.cuda.empty_cache()
            return fallback

        except torch.cuda.OutOfMemoryError:
            del ids, mask, spans
            # Drain deferred D2H pipeline — pending GPU tensors pin
            # allocator blocks that empty_cache() cannot release.
            self._d2h_overflow.update(self._d2h.finalize())
            _force_gc(self.device, threshold_mb=0)
            stats.oom_retries += 1
            return self._handle_oom(docs, indices, stats, _depth)

    def _handle_oom(self, docs, indices, stats, _depth):
        if self.device == "cuda":
            torch.cuda.synchronize()
            torch.cuda.empty_cache()
            torch.cuda.ipc_collect()

        if len(indices) <= 1:
            doc = docs[indices[0]]
            return self._process_single_windowed_safe(
                doc, self._estimate_max_single_seq(), stats,
            )
        if _depth >= 10:
            logger.error(f"OOM max depth reached. Skipping {len(indices)} docs.")
            dim = self.truncate_dim or 1024
            return {
                docs[di].doc_id: DocumentEmbeddings(
                    docs[di].doc_id, docs[di].chunk_texts,
                    np.zeros((max(len(docs[di].chunks), 1), dim), dtype=np.float32),
                )
                for di in indices
            }
        total_tok = sum(docs[i].n_tokens for i in indices)
        logger.warning(f"OOM on {len(indices)} docs ({total_tok:,} tok). Splitting…")
        result = {}
        for sub in self.batcher.split_batch(indices, docs=docs):
            r = self._process_batch(docs, sub, stats, _depth + 1)
            if r:
                result.update(r)
        return result

    # --- Main entry point ---

    def embed_documents(
        self,
        documents: list[dict],
        chunking: str = "semantic",
        show_progress: bool = True,
        target_chunk_tokens: int = 768,
        min_chunk_tokens: int = 256,
        max_chunk_tokens: int = 1536,
        similarity_percentile: float = 25.0,
    ) -> list[DocumentEmbeddings]:
        """
        Embed documents with late chunking.

        Args:
            documents: list of {"doc_id": str, "text": str}
            chunking: "sentences", "paragraphs", or "semantic"
            show_progress: show tqdm progress bar
            target_chunk_tokens: soft target chunk size (default 768 ≈ 90s speech)
            min_chunk_tokens: hard minimum chunk size (default 256)
            max_chunk_tokens: hard maximum chunk size (default 1536 ≈ 3 min speech)
            similarity_percentile: split threshold — lower = fewer, larger chunks
                (default 25.0, tuned for lecture topic continuity)
        """
        t0 = time.time()
        stats = BatchStats(n_docs=len(documents))
        self._d2h.reset()
        self._d2h_overflow.clear()

        prepare_chunking = "sentences" if chunking == "semantic" else chunking
        docs = self._prepare_documents(documents, prepare_chunking)
        total_tok = sum(d.n_tokens for d in docs)
        max_len   = max(d.n_tokens for d in docs) if docs else 0
        logger.info(
            f"Prepared {len(docs)} docs: {total_tok:,} tok, "
            f"max {max_len}, budget {self._max_batch_tokens:,}"
        )

        batches = self.batcher.create_batches(docs)
        logger.info(f"{len(batches)} batches, sizes: {[len(b) for b in batches]}")

        semantic_kwargs = None
        if chunking == "semantic":
            semantic_kwargs = dict(
                target_chunk_tokens=target_chunk_tokens,
                min_chunk_tokens=min_chunk_tokens,
                max_chunk_tokens=max_chunk_tokens,
                similarity_percentile=similarity_percentile,
            )

        sync_results = {}
        it = tqdm(batches, desc="Embedding", unit="batch") if show_progress else batches

        for batch_idx in it:
            r = self._process_batch(docs, batch_idx, stats,
                                    semantic_kwargs=semantic_kwargs)
            if r:
                sync_results.update(r)
            if show_progress and isinstance(it, tqdm):
                it.set_postfix(
                    {"tok/s": f"{stats.tokens_per_sec:,.0f}",
                     "ch/s":  f"{stats.chunks_per_sec:,.0f}",
                     "oom":   stats.oom_retries},
                    refresh=True,
                )

        async_results = self._d2h.finalize()
        results_map = {**sync_results, **async_results, **self._d2h_overflow}
        self._d2h_overflow.clear()

        stats.elapsed_s = time.time() - t0
        self.last_stats = stats

        # Free tokenized tensors and return cached GPU memory to driver
        for d in docs:
            d.token_ids = d.attention_mask = None
            d.chunks = []
        del docs, batches
        _force_gc(self.device, threshold_mb=0)

        results = [results_map[d["doc_id"]] for d in documents]
        logger.info(f"Done: {stats}")
        return results

    def get_stats(self) -> Optional[BatchStats]:
        return self.last_stats
