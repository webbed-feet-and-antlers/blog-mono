"""Stage 3 — assemble MTEB-format retrieval dataset (pure local, no LLM).

Reads ``intermediate/chunks.jsonl`` and ``intermediate/queries.jsonl`` and
writes the standard MTEB custom-dataset layout::

    datasets/govreport_retrieval/
        corpus.jsonl      → {"_id": "<chunk_id>", "title": "...", "text": "..."}
        queries.jsonl     → {"_id": "<query_id>", "text": "..."}
        qrels/test.tsv    → query-id\\tcorpus-id\\tscore  (with header)

Each query maps to exactly one gold chunk (its source). MTEB treats all
unlisted corpus items as negatives implicitly — no explicit negatives needed.

Verbatim soft-check: warn on chunks whose text is not a substring of the
source report. (Reports aren't loaded here — we only check chunk-to-chunk
duplicates.)
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from scripts.common import (
        CHUNKS_JSONL,
        DATASETS_DIR,
        INTERMEDIATE_DIR,
        QUERIES_JSONL,
        ensure_dirs,
        setup_logging,
    )
    from scripts.dataset_io import (
        DatasetStats,
        read_jsonl,
        validate_mteb_dir,
        write_jsonl,
        write_qrels_tsv,
    )
else:
    from ..scripts.common import (
        CHUNKS_JSONL,
        DATASETS_DIR,
        INTERMEDIATE_DIR,
        QUERIES_JSONL,
        ensure_dirs,
        setup_logging,
    )
    from ..scripts.dataset_io import (
        DatasetStats,
        read_jsonl,
        validate_mteb_dir,
        write_jsonl,
        write_qrels_tsv,
    )

logger = logging.getLogger(__name__)

DEFAULT_DATASET_NAME = "govreport_retrieval"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Stage 3 — assemble MTEB-format dataset")
    p.add_argument(
        "--dataset-name", default=DEFAULT_DATASET_NAME,
        help=f"Subdirectory under datasets/ (default: {DEFAULT_DATASET_NAME})",
    )
    p.add_argument(
        "--strict-verbatim", action="store_true",
        help="Drop chunks whose text is not a near-verbatim copy of report body "
             "(SequenceMatcher.ratio() >= 0.85). Off by default (warn-only).",
    )
    p.add_argument("--verbose", action="store_true", help="DEBUG logging")
    return p


def build_dataset(args: argparse.Namespace) -> DatasetStats:
    ensure_dirs()

    if not CHUNKS_JSONL.is_file():
        sys.exit(f"Missing {CHUNKS_JSONL} — run stage 1 first.")
    if not QUERIES_JSONL.is_file():
        sys.exit(f"Missing {QUERIES_JSONL} — run stage 2 first.")

    chunks = list(read_jsonl(CHUNKS_JSONL))
    queries = list(read_jsonl(QUERIES_JSONL))
    logger.info("Loaded %d chunks, %d queries", len(chunks), len(queries))

    if not chunks:
        sys.exit("No chunks — aborting.")
    if not queries:
        sys.exit("No queries — aborting.")

    # Optional verbatim soft-check: detect chunk_text duplicates (a sign of
    # LLM repeating itself across chunks). We don't have the original report
    # text here (it'd require re-loading HF), so we warn on intra-corpus
    # near-duplicates.
    seen_texts: list[str] = []
    kept_chunks: list[dict] = []
    dropped_for_verbatim = 0
    for c in chunks:
        text = c["text"]
        is_dupe = False
        for prev in seen_texts:
            ratio = SequenceMatcher(None, text, prev).ratio()
            if ratio >= 0.85:
                logger.warning(
                    "verbatim duplicate: %s ≈ earlier chunk (ratio=%.2f)",
                    c["chunk_id"], ratio,
                )
                is_dupe = True
                break
        if is_dupe and args.strict_verbatim:
            dropped_for_verbatim += 1
            continue
        seen_texts.append(text)
        kept_chunks.append(c)
    if dropped_for_verbatim:
        logger.warning("Dropped %d chunks under --strict-verbatim", dropped_for_verbatim)

    # Index queries by chunk_id so we keep only chunks with at least one query.
    queries_by_chunk: dict[str, list[dict]] = {}
    for q in queries:
        queries_by_chunk.setdefault(q["chunk_id"], []).append(q)

    # Build corpus rows: only chunks that have ≥1 query (else they'd be
    # untestable gold negatives, which is fine in MTEB but wasteful).
    corpus_rows = []
    kept_ids: set[str] = set()
    for c in kept_chunks:
        if c["chunk_id"] not in queries_by_chunk:
            continue
        corpus_rows.append({
            "_id": c["chunk_id"],
            "title": c.get("title", ""),
            "text": c["text"],
        })
        kept_ids.add(c["chunk_id"])

    # Build queries rows + qrels in deterministic order (chunk order,
    # then intra-chunk query index encoded in the query_id).
    queries_rows: list[dict] = []
    qrels: list[tuple[str, str, float]] = []
    for c in kept_chunks:
        cid = c["chunk_id"]
        if cid not in queries_by_chunk:
            continue
        for q in queries_by_chunk[cid]:
            queries_rows.append({
                "_id": q["query_id"],
                "text": q["text"],
            })
            qrels.append((q["query_id"], cid, 1.0))

    if not corpus_rows or not queries_rows:
        sys.exit("Nothing to write — corpus or queries empty after filtering.")

    # Sanity: any duplicate _ids?
    cid_counts = Counter(r["_id"] for r in corpus_rows)
    dupes = [k for k, v in cid_counts.items() if v > 1]
    if dupes:
        sys.exit(f"Duplicate corpus _ids: {dupes[:5]}")
    qid_counts = Counter(r["_id"] for r in queries_rows)
    dupes_q = [k for k, v in qid_counts.items() if v > 1]
    if dupes_q:
        sys.exit(f"Duplicate query _ids: {dupes_q[:5]}")

    out_dir = DATASETS_DIR / args.dataset_name
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "qrels").mkdir(parents=True, exist_ok=True)

    n_corpus = write_jsonl(out_dir / "corpus.jsonl", corpus_rows)
    n_queries = write_jsonl(out_dir / "queries.jsonl", queries_rows)
    n_qrels = write_qrels_tsv(out_dir / "qrels" / "test.tsv", qrels)

    logger.info("Wrote dataset to %s", out_dir)
    logger.info("  corpus.jsonl:    %d rows", n_corpus)
    logger.info("  queries.jsonl:   %d rows", n_queries)
    logger.info("  qrels/test.tsv:  %d rows", n_qrels)

    return validate_mteb_dir(out_dir)


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    setup_logging(args.verbose)
    stats = build_dataset(args)
    print(f"\n{stats}", file=sys.stderr)


if __name__ == "__main__":
    main()
