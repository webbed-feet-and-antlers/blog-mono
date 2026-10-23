"""CLI — one asyncio.run per process (a second one kills the shared
AsyncOpenAI client), semaphore-bounded concurrency everywhere.

    uv run python -m kg_data grounding
    uv run python -m kg_data generate --worlds 2 --docs-per-world 40
    uv run python -m kg_data adapt --cap 3000 --enrich 300
    uv run python -m kg_data silver --source enron --docs 200
    uv run python -m kg_data extract --split val
    uv run python -m kg_data answer --split val
    uv run python -m kg_data score --split val
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path

from . import adapters, audit, docgen, evalgen, export, extract as extract_mod
from . import grounding, score as score_mod, silver, worldgen
from .config import DATA_DIR, settings

log = logging.getLogger("kg_data")


def _seed_for(world_index: int) -> int:
    return settings.master_seed + 1009 * world_index


def _exit_clean() -> None:
    """HF Hub / fsspec leave non-daemon background threads that wedge
    interpreter shutdown after big streaming downloads; flush and leave."""
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0)


async def cmd_grounding(args: argparse.Namespace) -> None:
    summary = await grounding.prepare_all_async()
    print(json.dumps(summary, indent=1))
    _exit_clean()


async def cmd_generate(args: argparse.Namespace) -> None:
    exemplars: dict[str, str] = {}
    for channel in ("slack", "confluence", "drive", "email", "meeting_notes"):
        ex = grounding.load_exemplar_for(channel, seed=settings.master_seed)
        if ex:
            exemplars[channel] = ex

    worlds, all_docs, all_quads, all_questions = [], [], [], []
    agg: dict[str, float] = {}
    for i in range(args.worlds):
        world = await worldgen.generate_world(
            f"w{i:02d}", _seed_for(i), use_llm=True)
        corpus = await docgen.generate_world_corpus(
            world, args.docs_per_world, seed=_seed_for(i),
            exemplars=exemplars, use_llm=True)
        audited = await audit.audit_world_corpus(
            world, corpus.docs, corpus.quads, use_llm=True,
            stats=corpus.stats)
        corpus.quads = audited
        questions = evalgen.generate_eval_questions(
            corpus.world, corpus.quads, corpus.docs,
            seed=_seed_for(i), max_total=args.max_questions)
        worlds.append(corpus.world)
        all_docs.extend(corpus.docs)
        all_quads.extend(corpus.quads)
        all_questions.extend(questions)
        for k, v in corpus.stats.items():
            agg[k] = agg.get(k, 0) + v
        log.info("world %s: %s", world.id, corpus.stats)

    manifest = export.export_corpus(
        worlds, all_docs, all_quads, all_questions, seed=settings.master_seed)
    agg["questions"] = len(all_questions)
    print(json.dumps({"stats": agg,
                      "splits": manifest["splits"]}, indent=1))


async def cmd_adapt(args: argparse.Namespace) -> None:
    docs, quads = await asyncio.to_thread(adapters.trex.adapt_trex, args.cap)
    counts = {"trex": adapters.trex.write_public(docs, quads, "trex")}
    if args.enrich:
        wd_docs, wd_quads = await asyncio.to_thread(
            adapters.trex.enrich_wikidata, args.enrich)
        counts["wikidata"] = adapters.trex.write_public(
            wd_docs, wd_quads, "wikidata") or {"skipped": "no quads aligned"}
    print(json.dumps(counts, indent=1))
    _exit_clean()


async def cmd_extract(args: argparse.Namespace) -> None:
    worlds = export.load_worlds()
    worlds_by_id = {w.id: w for w in worlds}
    docs = [d for d in export.load_docs(args.split) if d.deleted_at is None]
    sem = asyncio.Semaphore(settings.concurrency)

    async def one(doc):
        try:
            return await extract_mod.extract_doc(
                worlds_by_id.get(doc.world_id), doc, semaphore=sem)
        except Exception as exc:
            log.warning("extraction failed for %s: %s", doc.id, str(exc)[:120])
            return []

    results = await asyncio.gather(*(one(d) for d in docs[: args.cap or None]))
    preds = [q for r in results for q in r]
    out = DATA_DIR / f"predictions_{args.split or 'all'}.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w") as f:
        for q in preds:
            f.write(q.model_dump_json() + "\n")
    print(f"wrote {len(preds)} predicted quads for {len(docs)} docs → {out}")


async def cmd_answer(args: argparse.Namespace) -> None:
    questions = export.load_questions(args.split)
    docs = export.load_docs()
    sem = asyncio.Semaphore(settings.concurrency)

    async def one(q):
        return q.id, await extract_mod.answer_question(q, docs, semaphore=sem)

    results = await asyncio.gather(*(one(q) for q in questions))
    out = DATA_DIR / f"answers_{args.split or 'all'}.jsonl"
    with out.open("w") as f:
        for qid, ans in results:
            f.write(json.dumps({"question_id": qid, "answer": ans}) + "\n")
    print(f"wrote {len(results)} answers → {out}")


async def cmd_score(args: argparse.Namespace) -> None:
    split = args.split
    worlds = export.load_worlds()
    gold = export.load_quads(split)
    docs = export.load_docs(split)
    pred_path = Path(args.pred or DATA_DIR / f"predictions_{split or 'all'}.jsonl")
    from .schema import Quad

    preds = [Quad.model_validate(json.loads(line))
             for line in pred_path.read_text().splitlines() if line] \
        if pred_path.exists() else []
    planned_gold = [q for q in gold if not q.discovered]
    result = {
        "quads_full": score_mod.score_quads(gold, preds, worlds),
        "quads_planned": score_mod.score_quads(planned_gold, preds, worlds),
        "stream": score_mod.score_stream(gold, preds, docs, worlds),
    }
    answers_path = DATA_DIR / f"answers_{split or 'all'}.jsonl"
    if answers_path.exists():
        answers = {json.loads(line)["question_id"]:
                   json.loads(line)["answer"]
                   for line in answers_path.read_text().splitlines() if line}
        questions = export.load_questions(split)
        result["questions"] = score_mod.score_questions(questions, answers, worlds)
    print(json.dumps(result, indent=1))


async def cmd_audit(args: argparse.Namespace) -> None:
    stats = await audit.apply_to_export(args.split, use_llm=True)
    print(json.dumps(stats, indent=1))
    from . import review

    out = review.build_review(args.split or "train")
    print(f"review page refreshed → {out}")


async def cmd_silver(args: argparse.Namespace) -> None:
    stats = await silver.run_silver(
        args.source, docs_cap=args.docs, votes=args.votes,
        use_llm=not args.no_llm, holdout=args.holdout)
    print(json.dumps(stats, indent=1, default=str))


async def cmd_models(args: argparse.Namespace) -> None:
    from . import models as models_mod

    rows = await asyncio.to_thread(models_mod.fetch_candidates)
    print(models_mod.render_table(rows))
    print("\ncurrent default:", settings.openrouter_model,
          "— bump config.py/OPENROUTER_MODEL if a newer/cheaper one wins")


async def cmd_review(args: argparse.Namespace) -> None:
    from . import review

    if args.silver:
        out = review.build_silver_review(args.silver)
        print(f"wrote {out} — silver labels incl. holdout; "
              f"verdicts export to JSON")
        return
    out = review.build_review(args.split)
    print(f"wrote {out} — open it in a browser; verdicts export to JSON")


def main() -> None:
    parser = argparse.ArgumentParser(prog="kg_data")
    parser.add_argument("--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("grounding")

    p_gen = sub.add_parser("generate")
    p_gen.add_argument("--worlds", type=int, default=settings.worlds)
    p_gen.add_argument("--docs-per-world", type=int,
                       default=settings.docs_per_world)
    p_gen.add_argument("--max-questions", type=int, default=20)

    p_ad = sub.add_parser("adapt")
    p_ad.add_argument("--cap", type=int, default=3000)
    p_ad.add_argument("--enrich", type=int, default=300,
                      help="wikidata temporal quads to enrich (0 to skip)")

    p_si = sub.add_parser("silver")
    p_si.add_argument("--source", choices=list(silver.SILVER_SOURCES),
                      default="enron")
    p_si.add_argument("--docs", type=int, default=200,
                      help="trainable docs to label (holdout is extra)")
    p_si.add_argument("--votes", type=int, default=3,
                      help="self-consistency samples per doc")
    p_si.add_argument("--holdout", type=int, default=25,
                      help="held-out human-audit docs (0 to skip)")
    p_si.add_argument("--no-llm", action="store_true",
                      help="offline: label nothing, exercise wiring only")

    p_ex = sub.add_parser("extract")
    p_ex.add_argument("--split", choices=["train", "val", "test"], default="val")
    p_ex.add_argument("--cap", type=int, default=0, help="limit docs (0 = all)")

    p_an = sub.add_parser("answer")
    p_an.add_argument("--split", choices=["train", "val", "test"], default="val")

    p_sc = sub.add_parser("score")
    p_sc.add_argument("--split", choices=["train", "val", "test"])
    p_sc.add_argument("--pred", default=None)

    p_rv = sub.add_parser("review")
    p_rv.add_argument("--split", choices=["train", "val", "test"],
                      default="train")
    p_rv.add_argument("--silver", default=None,
                      help="review a silver_{source} export instead "
                           "(e.g. enron)")

    p_au = sub.add_parser("audit")
    p_au.add_argument("--split", choices=["train", "val", "test"],
                      help="audit one split's exported corpus in place")

    sub.add_parser("models")

    args = parser.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s")

    handlers = {
        "grounding": cmd_grounding, "generate": cmd_generate,
        "adapt": cmd_adapt, "extract": cmd_extract,
        "answer": cmd_answer, "score": cmd_score, "review": cmd_review,
        "audit": cmd_audit, "models": cmd_models, "silver": cmd_silver,
    }
    asyncio.run(handlers[args.command](args))


if __name__ == "__main__":
    main()
