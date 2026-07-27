"""Stage 7 — Reranking dataset (combined gen + build).

For each query Q (gold = chunk C in report R), sample K candidates
(1 gold + N within-report non-gold + M cross-report). DeepSeek scores each
candidate's relevance 0–3. Writes:

* ``intermediate/reranking_scores.jsonl`` — per-query candidate scores.
* ``datasets/govreport_reranking/test.jsonl`` — MTEB format:
  ``{query, positive: [str...], negative: [str...]}``.

Partition: score ≥ 2 → positive; score ≤ 1 → negative. The gold chunk is
force-injected into positive regardless of the LLM's score.
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
        QUERIES_JSONL,
        RERANKING_SCORES_JSONL,
        add_common_args,
        ensure_dirs,
        resolve_api_key,
        setup_logging,
    )
    from scripts.dataset_io import (
        append_jsonl,
        read_jsonl,
        read_jsonl_id_set,
        validate_reranking_dir,
        write_jsonl,
    )
    from scripts.deepseek_client import LLMJsonError, call_json
    from scripts.prompts import RERANKING_USER_TEMPLATE, SYSTEM
    from scripts.schemas import RerankingResponse
else:
    from ..scripts.cache import DiskCache
    from ..scripts.common import (
        CACHE_DIR,
        CHUNKS_JSONL,
        DATASETS_DIR,
        FAILURES_JSONL,
        QUERIES_JSONL,
        RERANKING_SCORES_JSONL,
        add_common_args,
        ensure_dirs,
        resolve_api_key,
        setup_logging,
    )
    from ..scripts.dataset_io import (
        append_jsonl,
        read_jsonl,
        read_jsonl_id_set,
        validate_reranking_dir,
        write_jsonl,
    )
    from ..scripts.deepseek_client import LLMJsonError, call_json
    from ..scripts.prompts import RERANKING_USER_TEMPLATE, SYSTEM
    from ..scripts.schemas import RerankingResponse

logger = logging.getLogger(__name__)

DEFAULT_DATASET_NAME = "govreport_reranking"
DEFAULT_CANDIDATES_PER_QUERY = 10
DEFAULT_WITHIN_REPORT_NEGATIVES = 4  # 1 gold + 4 within + 5 cross = 10
DEFAULT_CROSS_REPORT_NEGATIVES = 5
SEED = 20240720


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Stage 7 — Reranking (gen + build)")
    add_common_args(p)
    p.add_argument(
        "--candidates-per-query", type=int, default=DEFAULT_CANDIDATES_PER_QUERY,
        help=f"Candidates per query (gold + negatives) (default: {DEFAULT_CANDIDATES_PER_QUERY})",
    )
    p.add_argument(
        "--dataset-name", default=DEFAULT_DATASET_NAME,
        help=f"Subdirectory under datasets/ (default: {DEFAULT_DATASET_NAME})",
    )
    return p


def build_work_items(
    chunks: list[dict],
    queries: list[dict],
    *,
    candidates_per_query: int,
    seed: int = SEED,
) -> list[dict]:
    """For each query, sample the candidate set.

    Returns one work item per query:
        {query_id, query_text, gold_chunk_id, candidates: [{chunk_id, text}]}
    """
    rng = random.Random(seed)
    by_report: dict[str, list[dict]] = {}
    for c in chunks:
        by_report.setdefault(c["report_id"], []).append(c)
    report_ids = sorted(by_report.keys())
    chunks_by_id = {c["chunk_id"]: c for c in chunks}

    # Budget split: 1 gold, then within-report negatives, then cross-report.
    n_negatives = max(0, candidates_per_query - 1)
    n_within = min(DEFAULT_WITHIN_REPORT_NEGATIVES, n_negatives)
    n_cross = max(0, n_negatives - n_within)

    out: list[dict] = []
    for q in queries:
        gold_cid = q["chunk_id"]
        gold_chunk = chunks_by_id.get(gold_cid)
        if not gold_chunk:
            continue
        rid = gold_chunk["report_id"]
        same_report = [c for c in by_report.get(rid, []) if c["chunk_id"] != gold_cid]
        other_reports = [r for r in report_ids if r != rid]

        candidates: list[dict] = [{"chunk_id": gold_cid, "text": gold_chunk["text"], "gold": True}]

        # Within-report negatives.
        if same_report and n_within:
            picked = rng.sample(same_report, min(n_within, len(same_report)))
            for c in picked:
                candidates.append({"chunk_id": c["chunk_id"], "text": c["text"], "gold": False})

        # Cross-report negatives.
        if other_reports and n_cross:
            for _ in range(n_cross):
                other_rid = rng.choice(other_reports)
                c = rng.choice(by_report[other_rid])
                candidates.append({"chunk_id": c["chunk_id"], "text": c["text"], "gold": False})

        # Dedupe by chunk_id (preserving order; the gold always wins first).
        seen: set[str] = set()
        deduped: list[dict] = []
        for c in candidates:
            if c["chunk_id"] in seen:
                continue
            seen.add(c["chunk_id"])
            deduped.append(c)

        out.append({
            "query_id": q["query_id"],
            "query_text": q["text"],
            "gold_chunk_id": gold_cid,
            "candidates": deduped,
        })
    return out


async def score_query(
    client: httpx.AsyncClient,
    *,
    item: dict,
    model: str,
    api_key: str,
    sem: asyncio.Semaphore,
    cache: DiskCache,
    use_cache: bool,
) -> dict:
    """Score one query's candidates. Returns one row for the intermediate file."""
    candidates_json = json.dumps(
        [{"chunk_id": c["chunk_id"], "text": c["text"]} for c in item["candidates"]],
        ensure_ascii=False,
    )
    user = RERANKING_USER_TEMPLATE.format(
        query=item["query_text"],
        query_id=item["query_id"],
        candidates_json=candidates_json,
    )
    messages = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": user},
    ]
    est_tokens = (len(candidates_json) + len(item["query_text"]) + len(user)) // 4
    max_tokens = max(500, est_tokens + 800)

    try:
        resp = await call_json(
            client,
            model=model,
            messages=messages,
            temperature=0.0,
            max_tokens=max_tokens,
            response_model=RerankingResponse,
            sem=sem,
            cache=cache,
            api_key=api_key,
            use_cache=use_cache,
        )
    except LLMJsonError as e:
        raise RuntimeError(f"JSON failure: {e}") from e
    except Exception as e:
        raise RuntimeError(f"error: {e}") from e

    by_id = {s.chunk_id: s.score for s in resp.scores}
    scores: list[dict] = []
    for c in item["candidates"]:
        cid = c["chunk_id"]
        if cid not in by_id:
            raise RuntimeError(f"missing chunk_id={cid} in response")
        scores.append({"chunk_id": cid, "score": int(by_id[cid])})

    return {
        "query_id": item["query_id"],
        "query_text": item["query_text"],
        "gold_chunk_id": item["gold_chunk_id"],
        "scores": scores,
        "candidates": item["candidates"],  # chunk text needed for build step
        "model": model,
    }


async def run(args: argparse.Namespace) -> int:
    ensure_dirs()
    api_key = resolve_api_key(args.api_key)
    cache = DiskCache(CACHE_DIR)
    sem = asyncio.Semaphore(args.concurrency)

    if not CHUNKS_JSONL.is_file():
        sys.exit(f"Missing {CHUNKS_JSONL} — run stage 1 first.")
    if not QUERIES_JSONL.is_file():
        sys.exit(f"Missing {QUERIES_JSONL} — run stage 2 first.")

    chunks = list(read_jsonl(CHUNKS_JSONL))
    queries = list(read_jsonl(QUERIES_JSONL))
    logger.info("Loaded %d chunks, %d queries", len(chunks), len(queries))

    work_items = build_work_items(
        chunks, queries,
        candidates_per_query=args.candidates_per_query,
    )
    logger.info("Built %d work item(s)", len(work_items))

    if args.restart and RERANKING_SCORES_JSONL.exists():
        logger.info("--restart: truncating %s", RERANKING_SCORES_JSONL)
        RERANKING_SCORES_JSONL.unlink()
    done = read_jsonl_id_set(RERANKING_SCORES_JSONL, "query_id") if RERANKING_SCORES_JSONL.exists() else set()
    if done:
        logger.info("Resume: %d query/queries already scored — will skip.", len(done))

    todo = [w for w in work_items if w["query_id"] not in done]
    logger.info("To score: %d quer(y/ies)", len(todo))
    if not todo:
        logger.info("Nothing to do — all queries already scored.")
        _build_dataset(args)
        return 0

    RERANKING_SCORES_JSONL.parent.mkdir(parents=True, exist_ok=True)
    out_lock = asyncio.Lock()
    out_file = RERANKING_SCORES_JSONL.open("a", encoding="utf-8")
    total_written = 0

    timeout = httpx.Timeout(connect=10.0, read=120.0, write=10.0, pool=30.0)
    try:
        async with httpx.AsyncClient(timeout=timeout, http2=True) as client:
            async def task_for(item: dict) -> None:
                nonlocal total_written
                try:
                    row = await score_query(
                        client,
                        item=item,
                        model=args.model,
                        api_key=api_key,
                        sem=sem,
                        cache=cache,
                        use_cache=not args.no_cache,
                    )
                except Exception as e:
                    logger.error("%s: %s — skipping", item["query_id"], e)
                    append_jsonl(FAILURES_JSONL, {
                        "stage": "reranking",
                        "query_id": item["query_id"],
                        "error": str(e),
                    })
                    return

                async with out_lock:
                    out_file.write(json.dumps(row, ensure_ascii=False))
                    out_file.write("\n")
                    out_file.flush()
                    total_written += 1
                logger.info("%s: scored %d candidate(s)",
                            item["query_id"], len(row["scores"]))

            await atqdm.gather(
                *(task_for(w) for w in todo),
                desc="reranking",
                file=sys.stderr,
            )
    finally:
        out_file.flush()
        out_file.close()

    logger.info("Wrote %d new row(s) to %s", total_written, RERANKING_SCORES_JSONL)

    _build_dataset(args)
    return total_written


def _build_dataset(args: argparse.Namespace) -> None:
    if not RERANKING_SCORES_JSONL.is_file():
        sys.exit(f"Missing {RERANKING_SCORES_JSONL} — nothing to build from.")

    rows = list(read_jsonl(RERANKING_SCORES_JSONL))
    out_dir = DATASETS_DIR / args.dataset_name
    out_dir.mkdir(parents=True, exist_ok=True)

    mteb_rows: list[dict] = []
    for r in rows:
        query_text = r["query_text"]
        gold_cid = r["gold_chunk_id"]
        text_by_id = {c["chunk_id"]: c["text"] for c in r.get("candidates", [])}
        # If candidates missing (older intermediate rows), we can't partition.
        if not text_by_id:
            logger.warning("query %s has no candidate texts — skipping", r["query_id"])
            continue

        positives: list[str] = []
        negatives: list[str] = []
        for s in r["scores"]:
            cid = s["chunk_id"]
            text = text_by_id.get(cid)
            if text is None:
                logger.warning("query %s: missing text for %s — skipping candidate",
                               r["query_id"], cid)
                continue
            if s["score"] >= 2:
                positives.append(text)
            else:
                negatives.append(text)

        # Force-inject the gold into positive regardless of its LLM score.
        gold_text = text_by_id.get(gold_cid)
        if gold_text and gold_text not in positives:
            positives.insert(0, gold_text)
            # And remove it from negatives if it landed there.
            negatives = [t for t in negatives if t != gold_text]

        if not positives or not negatives:
            # Skip degenerate rows — validator requires non-empty both lists.
            logger.warning(
                "query %s: empty partition (pos=%d, neg=%d) — skipping",
                r["query_id"], len(positives), len(negatives),
            )
            continue

        mteb_rows.append({"query": query_text, "positive": positives, "negative": negatives})

    if not mteb_rows:
        sys.exit("No valid reranking rows after partition.")

    n = write_jsonl(out_dir / "test.jsonl", mteb_rows)
    logger.info("Wrote %d rows to %s", n, out_dir / "test.jsonl")

    stats = validate_reranking_dir(out_dir)
    print(f"\n{stats}", file=sys.stderr)


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    setup_logging(args.verbose)
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
