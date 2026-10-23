"""Stage 7 — export.

Splits are BY WORLD (a fictional company's whole lifecycle lives in one
split) so entity timelines never leak across train/val/test. Writes:

  synthetic/worlds.jsonl                  ground-truth timelines (with split)
  synthetic/corpus/{split}/docs.jsonl     all revisions per doc
  synthetic/gold/{split}/quads.jsonl      gold quads with offsets + bounds
  synthetic/evals/questions.jsonl         eval questions (split-tagged)
  synthetic/ingestion_log.jsonl           CDC stream (create/revise/delete)
  manifest.json                           sha256 per file + config snapshot
"""

from __future__ import annotations

import hashlib
import json
import random
from pathlib import Path

from .config import DATA_DIR, settings
from .schema import (
    Doc, EvalQuestion, Quad, Split, World, build_ingestion_log,
)

SYNTH_DIR = DATA_DIR / "synthetic"


def assign_splits(worlds: list[World], *, seed: int) -> None:
    """Seeded 80/10/10 by world; small pilots may leave val/test empty."""
    order = sorted(w.id for w in worlds)
    random.Random(seed).shuffle(order)
    n = len(order)
    n_val = max(1, round(n * 0.1)) if n >= 3 else 0
    n_test = max(1, round(n * 0.1)) if n >= 3 else 0
    split_of: dict[str, Split] = {}
    for i, wid in enumerate(order):
        if i < n_val:
            split_of[wid] = "val"
        elif i < n_val + n_test:
            split_of[wid] = "test"
        else:
            split_of[wid] = "train"
    for w in worlds:
        w.split = split_of[w.id]


def _write_jsonl(path: Path, rows: list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        for row in rows:
            f.write(row.model_dump_json() + "\n")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def export_corpus(
    worlds: list[World], docs: list[Doc], quads: list[Quad],
    questions: list[EvalQuestion], *, seed: int,
) -> dict:
    """Write the full synthetic corpus + manifest. Returns the manifest."""
    assign_splits(worlds, seed=seed)

    _write_jsonl(SYNTH_DIR / "worlds.jsonl", worlds)
    _write_jsonl(SYNTH_DIR / "ingestion_log.jsonl", build_ingestion_log(docs))
    _write_jsonl(SYNTH_DIR / "evals" / "questions.jsonl", questions)
    for split in ("train", "val", "test"):
        wids = {w.id for w in worlds if w.split == split}
        _write_jsonl(
            SYNTH_DIR / "corpus" / split / "docs.jsonl",
            [d for d in docs if d.world_id in wids])
        _write_jsonl(
            SYNTH_DIR / "gold" / split / "quads.jsonl",
            [q for q in quads if q.world_id in wids])

    files = sorted(
        p.relative_to(DATA_DIR).as_posix()
        for p in SYNTH_DIR.rglob("*.jsonl")
    )
    manifest = {
        "files": {f: _sha256(DATA_DIR / f) for f in files},
        "config": {
            "model": settings.openrouter_model,
            "seed": seed,
            "worlds": len(worlds),
            "docs": len(docs),
            "quads": len(quads),
            "questions": len(questions),
        },
        "splits": {
            w.id: w.split for w in sorted(worlds, key=lambda w: w.id)
        },
    }
    (DATA_DIR / "manifest.json").write_text(
        json.dumps(manifest, indent=1, sort_keys=True) + "\n")
    return manifest


# --- loaders (for extract/score CLI paths) -----------------------------------


def load_worlds() -> list[World]:
    return [World.model_validate(json.loads(line)) for line in
            (SYNTH_DIR / "worlds.jsonl").read_text().splitlines() if line]


def load_docs(split: str | None = None) -> list[Doc]:
    if split:
        path = SYNTH_DIR / "corpus" / split / "docs.jsonl"
        return [Doc.model_validate(json.loads(line))
                for line in path.read_text().splitlines() if line]
    out = []
    for split in ("train", "val", "test"):
        p = SYNTH_DIR / "corpus" / split / "docs.jsonl"
        if p.exists():
            out += [Doc.model_validate(json.loads(line))
                    for line in p.read_text().splitlines() if line]
    return out


def load_quads(split: str | None = None) -> list[Quad]:
    if split:
        path = SYNTH_DIR / "gold" / split / "quads.jsonl"
        return [Quad.model_validate(json.loads(line))
                for line in path.read_text().splitlines() if line]
    out = []
    for split in ("train", "val", "test"):
        p = SYNTH_DIR / "gold" / split / "quads.jsonl"
        if p.exists():
            out += [Quad.model_validate(json.loads(line))
                    for line in p.read_text().splitlines() if line]
    return out


def load_questions(split: str | None = None) -> list[EvalQuestion]:
    path = SYNTH_DIR / "evals" / "questions.jsonl"
    qs = [EvalQuestion.model_validate(json.loads(line))
          for line in path.read_text().splitlines() if line]
    if split:
        qs = [q for q in qs if q.split == split]
    return qs
