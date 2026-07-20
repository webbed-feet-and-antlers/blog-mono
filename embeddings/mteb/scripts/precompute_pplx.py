"""Precompute PPLX embeddings for every MTEB task input.

Run once on a GPU box (CPU works too, just slowly). Output::

    mteb/precomputed/pplx-embed-context-v1-0.6b__text__mc8000_d1024.npz

The ``.npz`` uses the same schema as :class:`evaluate.cache.EmbeddingCache`
(``texts`` + ``vectors`` arrays). The loader (:class:`PrecomputedEncoder`)
treats it as the source of truth: a missing input text is a hard error at
evaluation time, so this producer must cover every text the evaluator will
ask about.

Design notes
------------

1. **Single file per ``(model, max_chars, dim)``.** PPLX uses no
   E5/BGE-style ``query:`` / ``passage:`` prefixes
   (see ``modules/runtime.py`` + ``modules/constants.py``), so every
   ``kind`` returns identical vectors. We encode each unique text once and
   let the loader map all kinds to this single ``__text__`` file.

2. **Force single-chunk encoding.** The runtime's default
   ``target_chunk_tokens=768`` could split long GovReport corpus chunks
   into 2+ chunks and yield multi-row ``DocumentEmbeddings``. We pass
   ``target_chunk_tokens=1_000_000`` (effectively unbounded) so each input
   text maps to exactly one embedding. (PPLX ``max_seq_len=32768`` tokens
   ≈ 120k chars is well above the MTEB ``max_chars=8000`` ceiling.) Any
   input that still produces >1 chunk is mean-pooled with a warning. If
   >5% of inputs multi-chunk, we exit non-zero (defensive — would
   indicate a real issue).
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import numpy as np

# Repo path setup — mirror evaluate/__main__.py so sibling packages resolve.
_HERE = Path(__file__).resolve().parent
_MTEB_DIR = _HERE.parent
_EMBEDDINGS_DIR = _MTEB_DIR.parent
if str(_MTEB_DIR) not in sys.path:
    sys.path.insert(0, str(_MTEB_DIR))
# Make `from modules.runtime import PPLXEmbedFP8Runtime` resolve.
if str(_EMBEDDINGS_DIR) not in sys.path:
    sys.path.insert(0, str(_EMBEDDINGS_DIR))

from scripts.common import DATASETS_DIR  # noqa: E402
from scripts.dataset_io import read_jsonl  # noqa: E402

from evaluate.cache import EmbeddingCache, _sig  # noqa: E402
from evaluate.encoders import DEFAULT_MAX_CHARS  # noqa: E402
from evaluate.tasks import ALL_TASKS, TASK_DIRS  # noqa: E402

logger = logging.getLogger("precompute_pplx")


# ----- Task → text-field mapping --------------------------------------------


def _texts_for_task(datasets_dir: Path, task: str) -> list[str]:
    """Return every input text the evaluator will encode for *task*.

    Sources mirror :mod:`evaluate.tasks` — same fields, same record layout.
    Returns ``[]`` if the task's dataset directory is missing (the evaluator
    skips missing datasets, so we skip them here too).
    """
    dd = datasets_dir / TASK_DIRS[task]
    if not dd.exists():
        logger.warning("Dataset for %s missing at %s — skipping", task, dd)
        return []

    out: list[str] = []
    if task in ("retrieval", "cross_report"):
        for row in read_jsonl(dd / "corpus.jsonl"):
            out.append(row["text"])
        for row in read_jsonl(dd / "queries.jsonl"):
            out.append(row["text"])
    elif task in ("sts", "summary_sts"):
        for row in read_jsonl(dd / "test.jsonl"):
            out.append(row["sent1"])
            out.append(row["sent2"])
    elif task == "clustering":
        for row in read_jsonl(dd / "test.jsonl"):
            out.append(row["text"])
    elif task == "reranking":
        for row in read_jsonl(dd / "test.jsonl"):
            out.append(row["query"])
            out.extend(row["positive"])
            out.extend(row["negative"])
    elif task == "pair_classification":
        for row in read_jsonl(dd / "test.jsonl"):
            out.append(row["sent1"])
            out.append(row["sent2"])
    else:
        logger.warning("Unknown task %r — skipping", task)
    return out


def enumerate_task_texts(
    datasets_dir: Path, tasks: list[str]
) -> list[str]:
    """Walk every selected task dataset and return the deduped text set.

    Order is deterministic (insertion order) so encode order is stable across
    runs. The loader's lookup is text-based, so it doesn't matter which task
    a text came from — same text → same vector.
    """
    seen: set[str] = set()
    out: list[str] = []
    total_raw = 0
    for task in tasks:
        for t in _texts_for_task(datasets_dir, task):
            total_raw += 1
            if t not in seen:
                seen.add(t)
                out.append(t)
    logger.info(
        "Enumerated %d unique texts across %d tasks (%d raw, %d dupes)",
        len(out), len(tasks), total_raw, total_raw - len(out),
    )
    return out


# ----- Main -----------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="precompute_pplx",
        description="Precompute PPLX embeddings for every MTEB task input.",
    )
    p.add_argument(
        "--datasets-dir", type=Path, default=DATASETS_DIR,
        help=f"Path to datasets/ folder (default: {DATASETS_DIR})",
    )
    p.add_argument(
        "--out-dir", type=Path, default=_MTEB_DIR / "precomputed",
        help="Output directory for the .npz file (default: mteb/precomputed)",
    )
    p.add_argument(
        "--device", default="cuda",
        help="Device for PPLXEmbedFP8Runtime (default: cuda; use cpu for smoke)",
    )
    p.add_argument(
        "--model", default="perplexity-ai/pplx-embed-context-v1-0.6b",
        help="HF model id (must match modules.constants.MODEL_ID)",
    )
    p.add_argument(
        "--truncate-dim", type=int, default=None,
        help="Truncate embeddings to this dim (default: native model dim, 1024)",
    )
    p.add_argument(
        "--max-chars", type=int, default=DEFAULT_MAX_CHARS,
        help=f"Truncate input texts to this many chars (default: {DEFAULT_MAX_CHARS})",
    )
    p.add_argument(
        "--tasks", default=",".join(ALL_TASKS),
        help=f"Comma-separated task names to cover (default: all 7). "
             f"Useful for smoke tests: --tasks sts",
    )
    p.add_argument(
        "--chunking", default="semantic",
        help="Chunker for PPLX runtime (default: semantic). "
             "Producer forces target_chunk_tokens=1_000_000 regardless.",
    )
    p.add_argument(
        "--force", action="store_true",
        help="Delete an existing output file before writing",
    )
    p.add_argument(
        "--verbose", action="store_true",
        help="DEBUG logging",
    )
    return p.parse_args(argv)


def _parse_tasks(raw: str) -> list[str]:
    out: list[str] = []
    for tok in raw.split(","):
        tok = tok.strip()
        if not tok:
            continue
        if tok not in ALL_TASKS:
            sys.exit(f"Unknown task {tok!r}. Choices: {list(ALL_TASKS)}")
        out.append(tok)
    if not out:
        sys.exit("No tasks selected — check --tasks")
    return out


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        stream=sys.stderr,
    )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    _setup_logging(args.verbose)

    tasks = _parse_tasks(args.tasks)
    datasets_dir = args.datasets_dir.resolve()
    out_dir = args.out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    # Resolve dim now — it determines the output filename and must match the
    # loader's expectation. If --truncate-dim is unset, use the model's
    # native dim (1024 for pplx-embed-context-v1-0.6b).
    truncate_dim = args.truncate_dim
    effective_dim = truncate_dim if truncate_dim is not None else 1024

    out_path = out_dir / _sig(args.model, "text", args.max_chars, effective_dim)
    if out_path.exists():
        if args.force:
            logger.info("--force: removing existing %s", out_path)
            out_path.unlink()
        else:
            logger.info(
                "Output %s already exists (%d bytes). "
                "Use --force to overwrite. Exiting.",
                out_path.name, out_path.stat().st_size,
            )
            return 0

    # 1. Enumerate texts.
    texts = enumerate_task_texts(datasets_dir, tasks)
    if not texts:
        logger.error(
            "No texts found under %s for tasks %s — did the dataset pipeline run?",
            datasets_dir, tasks,
        )
        return 1

    # 2. Build runtime.
    try:
        from modules.runtime import PPLXEmbedFP8Runtime
    except ImportError as e:
        logger.error(
            "Cannot import PPLXEmbedFP8Runtime from modules.runtime: %s. "
            "Run this script from the embeddings/ directory (or ensure "
            "embeddings/ is on PYTHONPATH) with the project's extras installed.",
            e,
        )
        return 1

    logger.info(
        "Loading PPLXEmbedFP8Runtime (device=%s, truncate_dim=%s)",
        args.device, truncate_dim,
    )
    runtime = PPLXEmbedFP8Runtime(
        device=args.device,
        truncate_dim=truncate_dim,
    )

    # 3. Encode. Force single-chunk per input by setting an enormous
    #    target_chunk_tokens — PPLX max_seq_len=32768 tokens ≈ 120k chars is
    #    well above our 8000-char ceiling.
    logger.info("Encoding %d unique texts…", len(texts))
    documents = [{"doc_id": str(i), "text": t} for i, t in enumerate(texts)]
    results = runtime.embed_documents(
        documents,
        chunking=args.chunking,
        target_chunk_tokens=1_000_000,
        show_progress=True,
    )

    # 4. Walk results; mean-pool any multi-chunk outputs.
    vectors = np.empty((len(texts), effective_dim), dtype=np.float32)
    multi_chunk_count = 0
    for i, doc_result in enumerate(results):
        emb = np.asarray(doc_result.embeddings, dtype=np.float32)
        if emb.ndim != 2 or emb.shape[0] == 0:
            logger.error(
                "Empty / malformed embeddings for doc_id=%s (shape=%s)",
                doc_result.doc_id, emb.shape,
            )
            return 1
        if emb.shape[1] != effective_dim:
            logger.error(
                "Dim mismatch for doc_id=%s: got %d, expected %d",
                doc_result.doc_id, emb.shape[1], effective_dim,
            )
            return 1
        if emb.shape[0] == 1:
            vectors[i] = emb[0]
        else:
            multi_chunk_count += 1
            logger.warning(
                "doc_id=%s produced %d chunks — mean-pooling "
                "(target_chunk_tokens=1_000_000 should prevent this)",
                doc_result.doc_id, emb.shape[0],
            )
            vectors[i] = emb.mean(axis=0)

    # 5. Defensive threshold: >5% multi-chunk indicates a real issue.
    multi_pct = (multi_chunk_count / len(texts)) * 100 if texts else 0.0
    if multi_pct > 5.0:
        logger.error(
            "%.1f%% of inputs multi-chunked — aborting (threshold 5%%). "
            "Check the chunking settings.", multi_pct,
        )
        return 1

    # 6. Write via EmbeddingCache (same on-disk schema the loader reads).
    cache = EmbeddingCache(out_path, dim=effective_dim)
    cache.put_many(texts, vectors)
    cache.flush()
    logger.info(
        "Wrote %d vectors (%dD, %d multi-chunked ≈ %.1f%%) → %s "
        "(%.1f MB)",
        len(texts), effective_dim, multi_chunk_count, multi_pct,
        out_path.name, out_path.stat().st_size / 1e6,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
