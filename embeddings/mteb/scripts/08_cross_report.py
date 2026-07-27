"""Stage 8 — Cross-report retrieval dataset (combined gen + build).

For each query Q (gold = chunk C in report R), the LLM identifies 0–2
ADDITIONAL gold chunks in reports ≠ R. Builds an MTEB retrieval dataset with
the same corpus as standard retrieval, but qrels include both the original
gold chunk AND any LLM-found cross-report positives.

Writes:
* ``intermediate/cross_report_qrels.jsonl`` — per-query LLM judgements.
* ``datasets/govreport_cross_report/`` — corpus.jsonl, queries.jsonl, qrels/test.tsv.
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
        CROSS_REPORT_QRELS_JSONL,
        QUERIES_JSONL,
        add_common_args,
        ensure_dirs,
        resolve_api_key,
        setup_logging,
    )
    from scripts.dataset_io import (
        append_jsonl,
        read_jsonl,
        read_jsonl_id_set,
        validate_cross_report_dir,
        write_jsonl,
        write_qrels_tsv,
    )
    from scripts.deepseek_client import LLMJsonError, call_json
    from scripts.prompts import CROSS_REPORT_USER_TEMPLATE, SYSTEM
    from scripts.schemas import CrossReportResponse
else:
    from ..scripts.cache import DiskCache
    from ..scripts.common import (
        CACHE_DIR,
        CHUNKS_JSONL,
        DATASETS_DIR,
        FAILURES_JSONL,
        CROSS_REPORT_QRELS_JSONL,
        QUERIES_JSONL,
        add_common_args,
        ensure_dirs,
        resolve_api_key,
        setup_logging,
    )
    from ..scripts.dataset_io import (
        append_jsonl,
        read_jsonl,
        read_jsonl_id_set,
        validate_cross_report_dir,
        write_jsonl,
        write_qrels_tsv,
    )
    from ..scripts.deepseek_client import LLMJsonError, call_json
    from ..scripts.prompts import CROSS_REPORT_USER_TEMPLATE, SYSTEM
    from ..scripts.schemas import CrossReportResponse

logger = logging.getLogger(__name__)

DEFAULT_DATASET_NAME = "govreport_cross_report"
DEFAULT_CANDIDATES_PER_QUERY = 10
SEED = 20240721


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Stage 8 — Cross-report retrieval (gen + build)")
    add_common_args(p)
    p.add_argument(
        "--candidates-per-query", type=int, default=DEFAULT_CANDIDATES_PER_QUERY,
        help=f"Cross-report candidates judged per query (default: {DEFAULT_CANDIDATES_PER_QUERY})",
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
    """For each query, sample N candidates from reports OTHER than the gold's report."""
    rng = random.Random(seed)
    by_report: dict[str, list[dict]] = {}
    for c in chunks:
        by_report.setdefault(c["report_id"], []).append(c)
    report_ids = sorted(by_report.keys())
    chunks_by_id = {c["chunk_id"]: c for c in chunks}

    out: list[dict] = []
    for q in queries:
        gold_chunk = chunks_by_id.get(q["chunk_id"])
        if not gold_chunk:
            continue
        rid = gold_chunk["report_id"]
        other_reports = [r for r in report_ids if r != rid]
        if not other_reports:
            continue

        candidates: list[dict] = []
        # Sample up to N distinct cross-report chunks.
        seen: set[str] = set()
        attempts = 0
        max_attempts = candidates_per_query * 5
        while len(candidates) < candidates_per_query and attempts < max_attempts:
            attempts += 1
            other_rid = rng.choice(other_reports)
            c = rng.choice(by_report[other_rid])
            if c["chunk_id"] in seen:
                continue
            seen.add(c["chunk_id"])
            candidates.append({"chunk_id": c["chunk_id"], "text": c["text"]})

        if not candidates:
            continue

        out.append({
            "query_id": q["query_id"],
            "query_text": q["text"],
            "gold_chunk_id": q["chunk_id"],
            "report_id": rid,
            "candidates": candidates,
        })
    return out


async def judge_query(
    client: httpx.AsyncClient,
    *,
    item: dict,
    model: str,
    api_key: str,
    sem: asyncio.Semaphore,
    cache: DiskCache,
    use_cache: bool,
) -> dict:
    candidates_json = json.dumps(
        [{"chunk_id": c["chunk_id"], "text": c["text"]} for c in item["candidates"]],
        ensure_ascii=False,
    )
    user = CROSS_REPORT_USER_TEMPLATE.format(
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
            response_model=CrossReportResponse,
            sem=sem,
            cache=cache,
            api_key=api_key,
            use_cache=use_cache,
        )
    except LLMJsonError as e:
        raise RuntimeError(f"JSON failure: {e}") from e
    except Exception as e:
        raise RuntimeError(f"error: {e}") from e

    by_id = {m.chunk_id: m.relevant for m in resp.matches}
    matches: list[dict] = []
    for c in item["candidates"]:
        cid = c["chunk_id"]
        if cid not in by_id:
            raise RuntimeError(f"missing chunk_id={cid} in response")
        matches.append({"chunk_id": cid, "relevant": bool(by_id[cid])})

    return {
        "query_id": item["query_id"],
        "query_text": item["query_text"],
        "gold_chunk_id": item["gold_chunk_id"],
        "matches": matches,
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

    if args.restart and CROSS_REPORT_QRELS_JSONL.exists():
        logger.info("--restart: truncating %s", CROSS_REPORT_QRELS_JSONL)
        CROSS_REPORT_QRELS_JSONL.unlink()
    done = (
        read_jsonl_id_set(CROSS_REPORT_QRELS_JSONL, "query_id")
        if CROSS_REPORT_QRELS_JSONL.exists()
        else set()
    )
    if done:
        logger.info("Resume: %d quer(y/ies) already judged — will skip.", len(done))

    todo = [w for w in work_items if w["query_id"] not in done]
    logger.info("To judge: %d quer(y/ies)", len(todo))
    if not todo:
        logger.info("Nothing to do — all queries already judged.")
        _build_dataset(args)
        return 0

    CROSS_REPORT_QRELS_JSONL.parent.mkdir(parents=True, exist_ok=True)
    out_lock = asyncio.Lock()
    out_file = CROSS_REPORT_QRELS_JSONL.open("a", encoding="utf-8")
    total_written = 0

    timeout = httpx.Timeout(connect=10.0, read=120.0, write=10.0, pool=30.0)
    try:
        async with httpx.AsyncClient(timeout=timeout, http2=True) as client:
            async def task_for(item: dict) -> None:
                nonlocal total_written
                try:
                    row = await judge_query(
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
                        "stage": "cross_report",
                        "query_id": item["query_id"],
                        "error": str(e),
                    })
                    return

                async with out_lock:
                    out_file.write(json.dumps(row, ensure_ascii=False))
                    out_file.write("\n")
                    out_file.flush()
                    total_written += 1
                n_pos = sum(1 for m in row["matches"] if m["relevant"])
                logger.info("%s: %d cross-report positive(s)",
                            item["query_id"], n_pos)

            await atqdm.gather(
                *(task_for(w) for w in todo),
                desc="cross-report",
                file=sys.stderr,
            )
    finally:
        out_file.flush()
        out_file.close()

    logger.info("Wrote %d new row(s) to %s", total_written, CROSS_REPORT_QRELS_JSONL)

    _build_dataset(args)
    return total_written


def _build_dataset(args: argparse.Namespace) -> None:
    if not CHUNKS_JSONL.is_file():
        sys.exit(f"Missing {CHUNKS_JSONL}.")
    if not QUERIES_JSONL.is_file():
        sys.exit(f"Missing {QUERIES_JSONL}.")
    if not CROSS_REPORT_QRELS_JSONL.is_file():
        sys.exit(f"Missing {CROSS_REPORT_QRELS_JSONL} — nothing to build from.")

    chunks = list(read_jsonl(CHUNKS_JSONL))
    queries = list(read_jsonl(QUERIES_JSONL))
    cross_rows = list(read_jsonl(CROSS_REPORT_QRELS_JSONL))

    # corpus = every chunk (MTEB treats unlisted items as negatives).
    corpus_rows = [
        {"_id": c["chunk_id"], "title": c.get("title", ""), "text": c["text"]}
        for c in chunks
    ]

    # queries rows + qrels: original gold + cross-report positives.
    queries_rows: list[dict] = []
    qrels: list[tuple[str, str, float]] = []
    cross_by_query = {r["query_id"]: r for r in cross_rows}

    for q in queries:
        qid = q["query_id"]
        queries_rows.append({"_id": qid, "text": q["text"]})
        # Original gold (always present).
        qrels.append((qid, q["chunk_id"], 1.0))
        # Cross-report positives from the LLM.
        cross = cross_by_query.get(qid)
        if cross:
            for m in cross["matches"]:
                if m["relevant"]:
                    qrels.append((qid, m["chunk_id"], 1.0))

    # Dedupe qrels (in case LLM finds the original gold's report sibling etc.).
    seen: set[tuple[str, str]] = set()
    deduped: list[tuple[str, str, float]] = []
    for qid, cid, score in qrels:
        if (qid, cid) in seen:
            continue
        seen.add((qid, cid))
        deduped.append((qid, cid, score))
    qrels = deduped

    out_dir = DATASETS_DIR / args.dataset_name
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "qrels").mkdir(parents=True, exist_ok=True)

    n_corpus = write_jsonl(out_dir / "corpus.jsonl", corpus_rows)
    n_queries = write_jsonl(out_dir / "queries.jsonl", queries_rows)
    n_qrels = write_qrels_tsv(out_dir / "qrels" / "test.tsv", qrels)
    logger.info(
        "Wrote dataset to %s (corpus=%d, queries=%d, qrels=%d)",
        out_dir, n_corpus, n_queries, n_qrels,
    )

    stats = validate_cross_report_dir(out_dir)
    print(f"\n{stats}", file=sys.stderr)


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    setup_logging(args.verbose)
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
