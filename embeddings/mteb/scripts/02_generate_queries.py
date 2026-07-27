"""Stage 2 — generate retrieval queries per chunk via DeepSeek.

Reads ``intermediate/chunks.jsonl`` and emits 1–3 analyst-style queries per
chunk to ``intermediate/queries.jsonl``.

Failures (after all retries + repair) are logged to
``intermediate/_failures.jsonl`` and skipped; the chunk is simply dropped from
the queries output. Stage 3 will only emit dataset entries for chunks that
produced queries.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import random
import sys
from pathlib import Path

import httpx
from tqdm.asyncio import tqdm as atqdm

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from scripts.cache import DiskCache
    from scripts.common import (
        CACHE_DIR,
        CHUNKS_JSONL,
        FAILURES_JSONL,
        INTERMEDIATE_DIR,
        QUERIES_JSONL,
        add_common_args,
        ensure_dirs,
        resolve_api_key,
        setup_logging,
    )
    from scripts.dataset_io import append_jsonl, read_jsonl, read_jsonl_id_set
    from scripts.deepseek_client import LLMJsonError, call_json
    from scripts.prompts import QUERY_USER_TEMPLATE, SYSTEM
    from scripts.schemas import QueryGenerationResponse
else:
    from ..scripts.cache import DiskCache
    from ..scripts.common import (
        CACHE_DIR,
        CHUNKS_JSONL,
        FAILURES_JSONL,
        INTERMEDIATE_DIR,
        QUERIES_JSONL,
        add_common_args,
        ensure_dirs,
        resolve_api_key,
        setup_logging,
    )
    from ..scripts.dataset_io import append_jsonl, read_jsonl, read_jsonl_id_set
    from ..scripts.deepseek_client import LLMJsonError, call_json
    from ..scripts.prompts import QUERY_USER_TEMPLATE, SYSTEM
    from ..scripts.schemas import QueryGenerationResponse

logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Stage 2 — LLM-generate queries per chunk")
    add_common_args(p)
    p.add_argument(
        "--sample-print", type=int, default=3,
        help="Print N random (chunk, queries) pairs to stderr for eyeball QA (default: 3)",
    )
    return p


async def gen_queries_for_chunk(
    client: httpx.AsyncClient,
    *,
    chunk: dict,
    model: str,
    api_key: str,
    sem: asyncio.Semaphore,
    cache: DiskCache,
    use_cache: bool,
) -> list[dict]:
    """Generate queries for one chunk. Returns [] on failure (caller logs)."""
    user = QUERY_USER_TEMPLATE.format(
        chunk_id=chunk["chunk_id"],
        title=chunk["title"],
        chunk_text=chunk["text"],
    )
    messages = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": user},
    ]
    # Generous ceiling; queries are short but answers can be a paragraph.
    max_tokens = max(800, (len(chunk["text"]) // 4) + 800)

    try:
        resp = await call_json(
            client,
            model=model,
            messages=messages,
            temperature=0.3,
            max_tokens=max_tokens,
            response_model=QueryGenerationResponse,
            sem=sem,
            cache=cache,
            api_key=api_key,
            use_cache=use_cache,
        )
    except LLMJsonError as e:
        raise RuntimeError(f"JSON failure: {e}") from e
    except Exception as e:
        raise RuntimeError(f"error: {e}") from e

    rows = []
    for i, q in enumerate(resp.queries):
        rows.append({
            "query_id": f"{chunk['chunk_id']}__q{i}",
            "chunk_id": chunk["chunk_id"],
            "report_id": chunk["report_id"],
            "text": q.query,
            "answer": q.answer,
        })
    return rows


async def run(args: argparse.Namespace) -> int:
    ensure_dirs()
    api_key = resolve_api_key(args.api_key)
    cache = DiskCache(CACHE_DIR)
    sem = asyncio.Semaphore(args.concurrency)

    if not CHUNKS_JSONL.is_file():
        sys.exit(f"Missing {CHUNKS_JSONL} — run stage 1 first (01_chunk_reports.py).")

    chunks = list(read_jsonl(CHUNKS_JSONL))
    logger.info("Loaded %d chunks from %s", len(chunks), CHUNKS_JSONL)

    # Resume support: if queries.jsonl already exists and --restart was NOT
    # passed, skip chunks whose queries are already on disk. Each chunk has
    # at most one query set, so chunk_id is the right dedup key.
    if args.restart and QUERIES_JSONL.exists():
        logger.info("--restart: truncating %s", QUERIES_JSONL)
        QUERIES_JSONL.unlink()
    if args.restart and FAILURES_JSONL.exists():
        FAILURES_JSONL.unlink()
    done_chunks = read_jsonl_id_set(QUERIES_JSONL, "chunk_id") if QUERIES_JSONL.exists() else set()
    if done_chunks:
        logger.info(
            "Resume: %d chunk(s) already in %s — will skip.",
            len(done_chunks), QUERIES_JSONL,
        )

    todo = [c for c in chunks if c["chunk_id"] not in done_chunks]
    logger.info(
        "To query: %d chunk(s) (%d skipped as already-done)",
        len(todo), len(chunks) - len(todo),
    )
    if not todo:
        logger.info("Nothing to do — all chunks already have queries.")
        return 0

    # per_chunk records only THIS run's results (used for sample-print).
    per_chunk: dict[str, list[dict]] = {}

    # Append-mode output, lock-guarded and per-row flushed.
    QUERIES_JSONL.parent.mkdir(parents=True, exist_ok=True)
    out_lock = asyncio.Lock()
    out_file = QUERIES_JSONL.open("a", encoding="utf-8")
    total_written = 0

    timeout = httpx.Timeout(connect=10.0, read=120.0, write=10.0, pool=30.0)
    try:
        async with httpx.AsyncClient(timeout=timeout, http2=True) as client:
            async def task_for(c: dict) -> None:
                nonlocal total_written
                try:
                    rows = await gen_queries_for_chunk(
                        client,
                        chunk=c,
                        model=args.model,
                        api_key=api_key,
                        sem=sem,
                        cache=cache,
                        use_cache=not args.no_cache,
                    )
                except Exception as e:
                    logger.error("%s: %s — logging failure and skipping", c["chunk_id"], e)
                    append_jsonl(FAILURES_JSONL, {
                        "chunk_id": c["chunk_id"],
                        "report_id": c["report_id"],
                        "error": str(e),
                    })
                    return

                # Stamp model on each row for traceability across model changes.
                for r in rows:
                    r["model"] = args.model

                per_chunk[c["chunk_id"]] = rows
                async with out_lock:
                    for row in rows:
                        out_file.write(json.dumps(row, ensure_ascii=False))
                        out_file.write("\n")
                        out_file.flush()
                    total_written += len(rows)
                logger.info("%s: wrote %d quer(y/ies)", c["chunk_id"], len(rows))

            await atqdm.gather(
                *(task_for(c) for c in todo),
                desc="queries",
                file=sys.stderr,
            )
    finally:
        out_file.flush()
        out_file.close()

    logger.info("Wrote %d new quer(y/ies) to %s", total_written, QUERIES_JSONL)
    if QUERIES_JSONL.exists():
        with QUERIES_JSONL.open("r", encoding="utf-8") as f:
            total = sum(1 for line in f if line.strip())
        logger.info("Total queries now on disk: %d", total)

    # Eyeball QA — sample-print N (chunk, queries) pairs from THIS run.
    if args.sample_print > 0 and per_chunk:
        sample_size = min(args.sample_print, len(per_chunk))
        chosen = random.sample(sorted(per_chunk.keys()), sample_size)
        chunks_by_id = {c["chunk_id"]: c for c in chunks}
        for cid in chosen:
            chunk = chunks_by_id.get(cid)
            qs = per_chunk.get(cid, [])
            if chunk is None:
                continue
            print("\n" + "=" * 60, file=sys.stderr)
            print(f"CHUNK {cid}  (title: {chunk.get('title')!r})", file=sys.stderr)
            print(f"CHUNK TEXT (first 200 chars): {chunk['text'][:200]!r}", file=sys.stderr)
            for q in qs:
                print(f"  Q: {q['text']}", file=sys.stderr)
                print(f"  A: {q['answer']}", file=sys.stderr)
        print("=" * 60 + "\n", file=sys.stderr)

    return total_written


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    setup_logging(args.verbose)
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
