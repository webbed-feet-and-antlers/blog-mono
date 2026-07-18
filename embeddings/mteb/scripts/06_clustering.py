"""Stage 6 — Clustering dataset (combined gen + build).

For each chunk in ``intermediate/chunks.jsonl``, the LLM assigns exactly one
topic from a fixed 15-category vocabulary. Writes:

* ``intermediate/topics.jsonl`` — per-chunk topic assignments.
* ``datasets/govreport_clustering/test.jsonl`` — MTEB format: ``{text, label}``.

Topic-vocab drift (the LLM emits something outside the vocab) is repaired by a
follow-up call; persistent failures are logged to ``_failures.jsonl`` and the
chunk is skipped.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
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
        DATASETS_DIR,
        FAILURES_JSONL,
        TOPICS_JSONL,
        add_common_args,
        ensure_dirs,
        resolve_api_key,
        setup_logging,
    )
    from scripts.dataset_io import (
        append_jsonl,
        read_jsonl,
        read_jsonl_id_set,
        validate_clustering_dir,
        write_jsonl,
    )
    from scripts.deepseek_client import LLMJsonError, call_json
    from scripts.prompts import CLUSTERING_USER_TEMPLATE, SYSTEM
    from scripts.schemas import TopicBatchResponse
else:
    from ..scripts.cache import DiskCache
    from ..scripts.common import (
        CACHE_DIR,
        CHUNKS_JSONL,
        DATASETS_DIR,
        FAILURES_JSONL,
        TOPICS_JSONL,
        add_common_args,
        ensure_dirs,
        resolve_api_key,
        setup_logging,
    )
    from ..scripts.dataset_io import (
        append_jsonl,
        read_jsonl,
        read_jsonl_id_set,
        validate_clustering_dir,
        write_jsonl,
    )
    from ..scripts.deepseek_client import LLMJsonError, call_json
    from ..scripts.prompts import CLUSTERING_USER_TEMPLATE, SYSTEM
    from ..scripts.schemas import TopicBatchResponse

logger = logging.getLogger(__name__)

DEFAULT_DATASET_NAME = "govreport_clustering"
DEFAULT_BATCH_SIZE = 5

# Fixed 15-topic vocabulary for U.S. government reports. Topic strings must be
# emitted verbatim by the LLM.
TOPIC_VOCAB: list[str] = [
    "Healthcare",
    "Defense & Military",
    "Environment & Energy",
    "Economy & Finance",
    "Education",
    "Technology & Telecom",
    "Justice & Law Enforcement",
    "Foreign Policy",
    "Homeland Security",
    "Housing & Urban Development",
    "Labor & Employment",
    "Science & Research",
    "Social Services",
    "Transportation",
    "Veterans Affairs",
]
TOPIC_VOCAB_SET = frozenset(TOPIC_VOCAB)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Stage 6 — Clustering (gen + build)")
    add_common_args(p)
    p.add_argument(
        "--batch-size", type=int, default=DEFAULT_BATCH_SIZE,
        help=f"Chunks per LLM call (default: {DEFAULT_BATCH_SIZE})",
    )
    p.add_argument(
        "--dataset-name", default=DEFAULT_DATASET_NAME,
        help=f"Subdirectory under datasets/ (default: {DEFAULT_DATASET_NAME})",
    )
    return p


def chunk_batches(items: list[dict], size: int) -> list[list[dict]]:
    return [items[i:i + size] for i in range(0, len(items), size)]


async def classify_batch(
    client: httpx.AsyncClient,
    *,
    batch: list[dict],
    model: str,
    api_key: str,
    sem: asyncio.Semaphore,
    cache: DiskCache,
    use_cache: bool,
) -> list[dict]:
    """Classify one batch of chunks. Returns rows to write to intermediate."""
    chunks_json = json.dumps(
        [{"chunk_id": c["chunk_id"], "text": c["text"]} for c in batch],
        ensure_ascii=False,
    )
    user = CLUSTERING_USER_TEMPLATE.format(
        topic_vocab="\n".join(f"  - {t}" for t in TOPIC_VOCAB),
        chunks_json=chunks_json,
    )
    messages = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": user},
    ]
    est_tokens = (len(chunks_json) + len(user)) // 4
    max_tokens = max(500, est_tokens + 800)

    try:
        resp = await call_json(
            client,
            model=model,
            messages=messages,
            temperature=0.0,
            max_tokens=max_tokens,
            response_model=TopicBatchResponse,
            sem=sem,
            cache=cache,
            api_key=api_key,
            use_cache=use_cache,
        )
    except LLMJsonError as e:
        raise RuntimeError(f"JSON failure: {e}") from e
    except Exception as e:
        raise RuntimeError(f"error: {e}") from e

    by_id = {a.chunk_id: a.topic for a in resp.assignments}
    rows: list[dict] = []
    for c in batch:
        if c["chunk_id"] not in by_id:
            raise RuntimeError(f"missing chunk_id={c['chunk_id']} in response")
        topic = by_id[c["chunk_id"]]
        if topic not in TOPIC_VOCAB_SET:
            raise RuntimeError(
                f"topic {topic!r} not in vocab (chunk_id={c['chunk_id']})"
            )
        rows.append({
            "chunk_id": c["chunk_id"],
            "topic": topic,
            "text": c["text"],
            "model": model,
        })
    return rows


async def run(args: argparse.Namespace) -> int:
    ensure_dirs()
    api_key = resolve_api_key(args.api_key)
    cache = DiskCache(CACHE_DIR)
    sem = asyncio.Semaphore(args.concurrency)

    if not CHUNKS_JSONL.is_file():
        sys.exit(f"Missing {CHUNKS_JSONL} — run stage 1 first.")

    chunks = list(read_jsonl(CHUNKS_JSONL))
    logger.info("Loaded %d chunks", len(chunks))

    if args.restart and TOPICS_JSONL.exists():
        logger.info("--restart: truncating %s", TOPICS_JSONL)
        TOPICS_JSONL.unlink()
    done = read_jsonl_id_set(TOPICS_JSONL, "chunk_id") if TOPICS_JSONL.exists() else set()
    if done:
        logger.info("Resume: %d chunk(s) already classified — will skip.", len(done))

    todo = [c for c in chunks if c["chunk_id"] not in done]
    batches = chunk_batches(todo, args.batch_size)
    logger.info(
        "To classify: %d chunk(s) in %d batch(es)",
        len(todo), len(batches),
    )
    if not todo:
        logger.info("Nothing to do — all chunks already classified.")
        _build_dataset(args)
        return 0

    TOPICS_JSONL.parent.mkdir(parents=True, exist_ok=True)
    out_lock = asyncio.Lock()
    out_file = TOPICS_JSONL.open("a", encoding="utf-8")
    total_written = 0

    timeout = httpx.Timeout(connect=10.0, read=120.0, write=10.0, pool=30.0)
    try:
        async with httpx.AsyncClient(timeout=timeout, http2=True) as client:
            async def task_for(batch: list[dict]) -> None:
                nonlocal total_written
                try:
                    rows = await classify_batch(
                        client,
                        batch=batch,
                        model=args.model,
                        api_key=api_key,
                        sem=sem,
                        cache=cache,
                        use_cache=not args.no_cache,
                    )
                except Exception as e:
                    logger.error("batch (chunk_ids=%s): %s — skipping",
                                 [c["chunk_id"] for c in batch], e)
                    for c in batch:
                        append_jsonl(FAILURES_JSONL, {
                            "stage": "clustering",
                            "chunk_id": c["chunk_id"],
                            "error": str(e),
                        })
                    return

                async with out_lock:
                    for row in rows:
                        out_file.write(json.dumps(row, ensure_ascii=False))
                        out_file.write("\n")
                        out_file.flush()
                    total_written += len(rows)
                logger.info("wrote %d classification(s)", len(rows))

            await atqdm.gather(
                *(task_for(b) for b in batches),
                desc="clustering",
                file=sys.stderr,
            )
    finally:
        out_file.flush()
        out_file.close()

    logger.info("Wrote %d new classification(s) to %s", total_written, TOPICS_JSONL)

    _build_dataset(args)
    return total_written


def _build_dataset(args: argparse.Namespace) -> None:
    if not TOPICS_JSONL.is_file():
        sys.exit(f"Missing {TOPICS_JSONL} — nothing to build from.")

    rows = list(read_jsonl(TOPICS_JSONL))
    out_dir = DATASETS_DIR / args.dataset_name
    out_dir.mkdir(parents=True, exist_ok=True)
    mteb_rows = [{"text": r["text"], "label": r["topic"]} for r in rows]
    n = write_jsonl(out_dir / "test.jsonl", mteb_rows)
    logger.info("Wrote %d rows to %s", n, out_dir / "test.jsonl")

    stats = validate_clustering_dir(out_dir)
    print(f"\n{stats}", file=sys.stderr)


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    setup_logging(args.verbose)
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
