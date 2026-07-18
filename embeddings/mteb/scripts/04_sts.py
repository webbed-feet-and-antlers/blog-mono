"""Stage 4 — STS dataset (combined gen + build).

Reads ``intermediate/chunks.jsonl``, samples within-report and cross-report
chunk-pairs, scores each pair 0–5 via DeepSeek, and writes:

* ``intermediate/sts_pairs.jsonl`` — full per-pair rows.
* ``datasets/govreport_sts/test.jsonl`` — MTEB-format: ``{sent1, sent2, score}``.

Resume-aware: pairs already in the intermediate file are skipped.
``--restart`` truncates the intermediate file and reruns.
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
        STS_PAIRS_JSONL,
        add_common_args,
        ensure_dirs,
        resolve_api_key,
        setup_logging,
    )
    from scripts.dataset_io import (
        append_jsonl,
        read_jsonl,
        read_jsonl_id_set,
        validate_sts_dir,
        write_jsonl,
    )
    from scripts.deepseek_client import LLMJsonError, call_json
    from scripts.prompts import STS_USER_TEMPLATE, SYSTEM
    from scripts.schemas import STSBatchResponse
else:
    from ..scripts.cache import DiskCache
    from ..scripts.common import (
        CACHE_DIR,
        CHUNKS_JSONL,
        DATASETS_DIR,
        FAILURES_JSONL,
        STS_PAIRS_JSONL,
        add_common_args,
        ensure_dirs,
        resolve_api_key,
        setup_logging,
    )
    from ..scripts.dataset_io import (
        append_jsonl,
        read_jsonl,
        read_jsonl_id_set,
        validate_sts_dir,
        write_jsonl,
    )
    from ..scripts.deepseek_client import LLMJsonError, call_json
    from ..scripts.prompts import STS_USER_TEMPLATE, SYSTEM
    from ..scripts.schemas import STSBatchResponse

logger = logging.getLogger(__name__)

DEFAULT_DATASET_NAME = "govreport_sts"
DEFAULT_PAIRS_PER_CHUNK = 2
DEFAULT_BATCH_SIZE = 5
SEED = 20240718


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Stage 4 — STS dataset (gen + build)")
    add_common_args(p)
    p.add_argument(
        "--pairs-per-chunk", type=int, default=DEFAULT_PAIRS_PER_CHUNK,
        help=f"Target pairs per chunk (default: {DEFAULT_PAIRS_PER_CHUNK}). "
             "1 = within-report only; 2 = within + cross-report.",
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
    *,
    pairs_per_chunk: int,
    seed: int = SEED,
) -> list[dict]:
    """Build a deterministic list of chunk-pairs.

    For each chunk C, samples one within-report pair (C + another chunk from
    C's report) and, when pairs_per_chunk >= 2, one cross-report pair
    (C + a random chunk from a different report). Returns one row per pair
    with a stable pair_id; the pair_id is symmetric so we dedupe.
    """
    rng = random.Random(seed)
    by_report: dict[str, list[dict]] = {}
    for c in chunks:
        by_report.setdefault(c["report_id"], []).append(c)
    report_ids = sorted(by_report.keys())

    def stable_pair_id(a: dict, b: dict) -> str:
        x, y = sorted([a["chunk_id"], b["chunk_id"]])
        return f"{x}__{y}"

    seen: set[str] = set()
    out: list[dict] = []

    for c in chunks:
        rid = c["report_id"]
        same_report_chunks = [x for x in by_report[rid] if x["chunk_id"] != c["chunk_id"]]
        # Within-report pair.
        if same_report_chunks:
            other = rng.choice(same_report_chunks)
            pid = stable_pair_id(c, other)
            if pid not in seen:
                seen.add(pid)
                out.append({
                    "pair_id": pid,
                    "sent1": c["text"],
                    "sent2": other["text"],
                    "kind": "within_report",
                })
        # Cross-report pair.
        if pairs_per_chunk >= 2:
            other_reports = [r for r in report_ids if r != rid]
            if other_reports and len(chunks) > 1:
                other_rid = rng.choice(other_reports)
                other = rng.choice(by_report[other_rid])
                pid = stable_pair_id(c, other)
                if pid not in seen:
                    seen.add(pid)
                    out.append({
                        "pair_id": pid,
                        "sent1": c["text"],
                        "sent2": other["text"],
                        "kind": "cross_report",
                    })

    return out


def chunk_batches(items: list[dict], size: int) -> list[list[dict]]:
    """Partition items into batches of <= size."""
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
    batch_size: int,
) -> list[dict]:
    """Score one batch of pairs. Returns rows to write to intermediate."""
    pairs_json = json.dumps(
        [{"pair_id": p["pair_id"], "text_a": p["sent1"], "text_b": p["sent2"]} for p in batch],
        ensure_ascii=False,
    )
    user = STS_USER_TEMPLATE.format(pairs_json=pairs_json)
    messages = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": user},
    ]
    est_tokens = (len(pairs_json) + len(user) + sum(len(p["sent1"]) + len(p["sent2"]) for p in batch)) // 4
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
    rows: list[dict] = []
    for p in batch:
        if p["pair_id"] not in by_id:
            raise RuntimeError(
                f"model did not return score for pair_id={p['pair_id']}"
            )
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
        sys.exit(f"Missing {CHUNKS_JSONL} — run stage 1 first (01_chunk_reports.py).")

    chunks = list(read_jsonl(CHUNKS_JSONL))
    logger.info("Loaded %d chunks from %s", len(chunks), CHUNKS_JSONL)

    all_pairs = sample_pairs(chunks, pairs_per_chunk=args.pairs_per_chunk)
    logger.info(
        "Sampled %d candidate pairs (target=%d/chunk)",
        len(all_pairs), args.pairs_per_chunk,
    )

    # Resume: skip pairs whose pair_id is already in the intermediate file.
    if args.restart and STS_PAIRS_JSONL.exists():
        logger.info("--restart: truncating %s", STS_PAIRS_JSONL)
        STS_PAIRS_JSONL.unlink()
    done = read_jsonl_id_set(STS_PAIRS_JSONL, "pair_id") if STS_PAIRS_JSONL.exists() else set()
    if done:
        logger.info("Resume: %d pair(s) already scored — will skip.", len(done))

    todo = [p for p in all_pairs if p["pair_id"] not in done]
    batches = chunk_batches(todo, args.batch_size)
    logger.info(
        "To score: %d pair(s) in %d batch(es) (batch_size=%d)",
        len(todo), len(batches), args.batch_size,
    )
    if not todo:
        logger.info("Nothing to do — all pairs already scored.")
        _build_dataset(args)
        return 0

    STS_PAIRS_JSONL.parent.mkdir(parents=True, exist_ok=True)
    out_lock = asyncio.Lock()
    out_file = STS_PAIRS_JSONL.open("a", encoding="utf-8")
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
                        batch_size=args.batch_size,
                    )
                except Exception as e:
                    logger.error("batch (pair_ids=%s): %s — skipping",
                                 [p["pair_id"] for p in batch], e)
                    for p in batch:
                        append_jsonl(FAILURES_JSONL, {
                            "stage": "sts",
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
                desc="sts-scoring",
                file=sys.stderr,
            )
    finally:
        out_file.flush()
        out_file.close()

    logger.info("Wrote %d new pair(s) to %s", total_written, STS_PAIRS_JSONL)

    _build_dataset(args)
    return total_written


def _build_dataset(args: argparse.Namespace) -> None:
    """Convert intermediate rows → MTEB-format files in datasets/<name>/."""
    if not STS_PAIRS_JSONL.is_file():
        sys.exit(f"Missing {STS_PAIRS_JSONL} — nothing to build from.")

    rows = list(read_jsonl(STS_PAIRS_JSONL))
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
