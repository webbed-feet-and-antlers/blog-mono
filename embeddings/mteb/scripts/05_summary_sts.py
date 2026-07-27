"""Stage 5 — Summary STS dataset (combined gen + build).

Reads ``intermediate/chunks.jsonl`` AND GovReport summaries (lazy-loaded from
HF). For each chunk C, samples one POSITIVE pair (C's report summary, C) and
one NEGATIVE pair (random other-report summary, C). Scores each pair 0–5 via
DeepSeek.

Writes:
* ``intermediate/summary_sts_pairs.jsonl`` — full per-pair rows.
* ``datasets/govreport_summary_sts/test.jsonl`` — MTEB STS format.
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
        DATASETS_DIR,
        FAILURES_JSONL,
        SUMMARY_STS_PAIRS_JSONL,
        add_common_args,
        ensure_dirs,
        resolve_api_key,
        setup_logging,
    )
    from scripts.dataset_io import (
        append_jsonl,
        load_govreport_subset,
        read_jsonl,
        read_jsonl_id_set,
        validate_sts_dir,
        write_jsonl,
    )
    from scripts.deepseek_client import LLMJsonError, call_json
    from scripts.prompts import SUMMARY_STS_USER_TEMPLATE, SYSTEM
    from scripts.schemas import STSBatchResponse
else:
    from ..scripts.cache import DiskCache
    from ..scripts.common import (
        CACHE_DIR,
        CHUNKS_JSONL,
        DATASETS_DIR,
        FAILURES_JSONL,
        SUMMARY_STS_PAIRS_JSONL,
        add_common_args,
        ensure_dirs,
        resolve_api_key,
        setup_logging,
    )
    from ..scripts.dataset_io import (
        append_jsonl,
        load_govreport_subset,
        read_jsonl,
        read_jsonl_id_set,
        validate_sts_dir,
        write_jsonl,
    )
    from ..scripts.deepseek_client import LLMJsonError, call_json
    from ..scripts.prompts import SUMMARY_STS_USER_TEMPLATE, SYSTEM
    from ..scripts.schemas import STSBatchResponse

logger = logging.getLogger(__name__)

DEFAULT_DATASET_NAME = "govreport_summary_sts"
DEFAULT_NEGATIVES_PER_CHUNK = 1
DEFAULT_BATCH_SIZE = 5
SEED = 20240719


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Stage 5 — Summary STS (gen + build)")
    add_common_args(p)
    p.add_argument(
        "--negatives-per-chunk", type=int, default=DEFAULT_NEGATIVES_PER_CHUNK,
        help=f"Negative (other-report summary) pairs per chunk (default: {DEFAULT_NEGATIVES_PER_CHUNK})",
    )
    p.add_argument(
        "--batch-size", type=int, default=DEFAULT_BATCH_SIZE,
        help=f"Pairs scored per LLM call (default: {DEFAULT_BATCH_SIZE})",
    )
    p.add_argument(
        "--dataset-name", default=DEFAULT_DATASET_NAME,
        help=f"Subdirectory under datasets/ (default: {DEFAULT_DATASET_NAME})",
    )
    return p


def sample_pairs(
    chunks: list[dict],
    summaries: dict[str, str],
    *,
    negatives_per_chunk: int,
    seed: int = SEED,
) -> list[dict]:
    """Build (summary, chunk) pairs.

    For each chunk, the POSITIVE pair is its own report's summary. NEGATIVE
    pairs use summaries from other reports.
    """
    rng = random.Random(seed)
    report_ids = sorted({c["report_id"] for c in chunks})
    other_reports = {
        rid: [r for r in report_ids if r != rid]
        for rid in report_ids
    }

    seen: set[str] = set()
    out: list[dict] = []

    def add_pair(summary: str, chunk: dict, kind: str) -> None:
        if not summary or not chunk["text"]:
            return
        pid = f"{kind}__{chunk['chunk_id']}__{abs(hash(summary)) % 100000}"
        if pid in seen:
            return
        seen.add(pid)
        out.append({
            "pair_id": pid,
            "sent1": summary,
            "sent2": chunk["text"],
            "kind": kind,
        })

    for c in chunks:
        rid = c["report_id"]
        own_summary = summaries.get(rid, "")
        if own_summary:
            add_pair(own_summary, c, "positive")
        for _ in range(negatives_per_chunk):
            others = other_reports.get(rid, [])
            if not others:
                continue
            other_rid = rng.choice(others)
            other_summary = summaries.get(other_rid, "")
            if other_summary:
                add_pair(other_summary, c, "negative")

    return out


def chunk_batches(items: list[dict], size: int) -> list[list[dict]]:
    return [items[i:i + size] for i in range(0, len(items), size)]


async def score_batch(
    client: httpx.AsyncClient,
    *,
    batch: list[dict],
    model: str,
    api_key: str,
    sem: asyncio.Semaphore,
    cache: DiskCache,
    use_cache: bool,
) -> list[dict]:
    pairs_json = json.dumps(
        [{"pair_id": p["pair_id"], "summary": p["sent1"], "passage": p["sent2"]} for p in batch],
        ensure_ascii=False,
    )
    user = SUMMARY_STS_USER_TEMPLATE.format(pairs_json=pairs_json)
    messages = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": user},
    ]
    est_tokens = (len(pairs_json) + len(user)) // 4
    max_tokens = max(500, est_tokens + 800)

    try:
        resp = await call_json(
            client,
            model=model,
            messages=messages,
            temperature=0.0,
            max_tokens=max_tokens,
            response_model=STSBatchResponse,
            sem=sem,
            cache=cache,
            api_key=api_key,
            use_cache=use_cache,
        )
    except LLMJsonError as e:
        raise RuntimeError(f"JSON failure: {e}") from e
    except Exception as e:
        raise RuntimeError(f"error: {e}") from e

    by_id = {s.pair_id: s.score for s in resp.scores}
    rows = []
    for p in batch:
        if p["pair_id"] not in by_id:
            raise RuntimeError(f"missing pair_id={p['pair_id']} in response")
        rows.append({
            "pair_id": p["pair_id"],
            "sent1": p["sent1"],
            "sent2": p["sent2"],
            "score": float(by_id[p["pair_id"]]),
            "kind": p.get("kind", ""),
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
    needed_reports = {c["report_id"] for c in chunks}
    logger.info("Loaded %d chunks; need summaries for %d report(s)", len(chunks), len(needed_reports))

    # Load summaries from GovReport. We re-load the subset and join on report_id.
    logger.info("Loading GovReport summaries (subset=%s, split=%s)...", args.subset, args.split)
    reports = load_govreport_subset(subset=args.subset, split=args.split, include_summary=True)
    summaries = {r["report_id"]: r.get("summary", "") for r in reports}
    missing = needed_reports - set(summaries.keys())
    if missing:
        logger.error(
            "Missing summaries for %d report(s) (subset too small?). First few: %s",
            len(missing), sorted(missing)[:5],
        )
        sys.exit("Re-run stage 1 with the same --subset, or increase --subset here.")

    empty = [rid for rid, s in summaries.items() if not s and rid in needed_reports]
    if empty:
        sys.exit(f"GovReport rows missing 'summary' field for: {empty[:5]}")

    all_pairs = sample_pairs(
        chunks, summaries,
        negatives_per_chunk=args.negatives_per_chunk,
    )
    logger.info("Sampled %d candidate (summary, chunk) pairs", len(all_pairs))

    if args.restart and SUMMARY_STS_PAIRS_JSONL.exists():
        logger.info("--restart: truncating %s", SUMMARY_STS_PAIRS_JSONL)
        SUMMARY_STS_PAIRS_JSONL.unlink()
    done = read_jsonl_id_set(SUMMARY_STS_PAIRS_JSONL, "pair_id") if SUMMARY_STS_PAIRS_JSONL.exists() else set()
    if done:
        logger.info("Resume: %d pair(s) already scored — will skip.", len(done))

    todo = [p for p in all_pairs if p["pair_id"] not in done]
    batches = chunk_batches(todo, args.batch_size)
    logger.info(
        "To score: %d pair(s) in %d batch(es)",
        len(todo), len(batches),
    )
    if not todo:
        logger.info("Nothing to do — all pairs already scored.")
        _build_dataset(args)
        return 0

    SUMMARY_STS_PAIRS_JSONL.parent.mkdir(parents=True, exist_ok=True)
    out_lock = asyncio.Lock()
    out_file = SUMMARY_STS_PAIRS_JSONL.open("a", encoding="utf-8")
    total_written = 0

    timeout = httpx.Timeout(connect=10.0, read=120.0, write=10.0, pool=30.0)
    try:
        async with httpx.AsyncClient(timeout=timeout, http2=True) as client:
            async def task_for(batch: list[dict]) -> None:
                nonlocal total_written
                try:
                    rows = await score_batch(
                        client,
                        batch=batch,
                        model=args.model,
                        api_key=api_key,
                        sem=sem,
                        cache=cache,
                        use_cache=not args.no_cache,
                    )
                except Exception as e:
                    logger.error("batch (pair_ids=%s): %s — skipping",
                                 [p["pair_id"] for p in batch], e)
                    for p in batch:
                        append_jsonl(FAILURES_JSONL, {
                            "stage": "summary_sts",
                            "pair_id": p["pair_id"],
                            "error": str(e),
                        })
                    return

                async with out_lock:
                    for row in rows:
                        out_file.write(json.dumps(row, ensure_ascii=False))
                        out_file.write("\n")
                        out_file.flush()
                    total_written += len(rows)
                logger.info("wrote %d scored pair(s)", len(rows))

            await atqdm.gather(
                *(task_for(b) for b in batches),
                desc="summary-sts",
                file=sys.stderr,
            )
    finally:
        out_file.flush()
        out_file.close()

    logger.info("Wrote %d new pair(s) to %s", total_written, SUMMARY_STS_PAIRS_JSONL)

    _build_dataset(args)
    return total_written


def _build_dataset(args: argparse.Namespace) -> None:
    if not SUMMARY_STS_PAIRS_JSONL.is_file():
        sys.exit(f"Missing {SUMMARY_STS_PAIRS_JSONL} — nothing to build from.")

    rows = list(read_jsonl(SUMMARY_STS_PAIRS_JSONL))
    out_dir = DATASETS_DIR / args.dataset_name
    out_dir.mkdir(parents=True, exist_ok=True)
    mteb_rows = [
        {"sent1": r["sent1"], "sent2": r["sent2"], "score": r["score"]}
        for r in rows
    ]
    n = write_jsonl(out_dir / "test.jsonl", mteb_rows)
    logger.info("Wrote %d rows to %s", n, out_dir / "test.jsonl")

    stats = validate_sts_dir(out_dir)
    print(f"\n{stats}", file=sys.stderr)


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    setup_logging(args.verbose)
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
