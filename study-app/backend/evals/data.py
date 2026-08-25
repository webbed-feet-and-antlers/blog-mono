"""Download + prepare the public datasets behind the eval suites.

    uv run python -m evals.data            # prepare everything (idempotent)

Everything lands in evals/data/ (gitignored) as small, normalized JSONL /
parquet samples so the suites never touch the network. Large upstream files
(Duolingo 13M traces, EdNet shards) are pulled into the Hugging Face cache
(~/.cache/huggingface, a few GB) — never into the repo. That cache is only
needed while (re-)preparing: afterwards it can be deleted, and manifest.json
below records sha256s of every prepared file so integrity stays checkable
without re-downloading.

Datasets (all public, research use; see evals/README.md for licenses):
  - SciQ            HF allenai/sciq        — passages + gold MCQs/distractors
  - RACE            HF ehovy/race          — passages + difficulty tiers
  - AL-CPL          GitHub harrylclc/AL-CPL-dataset + Wikipedia summaries
                                          — gold concepts + prerequisite pairs
  - PubMed (notes)  HF ccdv/pubmed-summarization — long docs + human abstracts
  - Duolingo HLR    HF Zai04/Duolingo-Spaced-Repetition-Data (settles.acl16)
                                          — real forgetting-curve traces
  - EdNet-KT1       HF mgor/EDNet          — real learner interaction logs

Splits: every dataset big enough is divided ONCE here into train/val/test
pools (seeded, disjoint — parquets split by whole user so no learner spans
a split). Suites draw from val by default (EVALS_SPLIT); test is the
held-out overfitting check, train is the scratch pool. See README
"Splits & overfitting" for the rationale. AL-CPL is 4 courses — too small
to split, so it ships as one unsplit file.
"""

from __future__ import annotations

import hashlib
import json
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from evals.config import DATA_DIR, SAMPLE_SEED

UA = {"User-Agent": "study-app-evals/0.1 (research; contact: dev)"}

AL_CPL_COURSES = ["data_mining", "geometry", "physics", "precalculus"]
AL_CPL_RAW = (
    "https://raw.githubusercontent.com/harrylclc/AL-CPL-dataset/master"
)

SPLITS = ("train", "val", "test")

# Prepared caps per dataset. Only a slice of each upstream pool is persisted
# (the suites draw at most ~25 cases per run); the caps keep the repo-adjacent
# footprint small and re-preparation fast while leaving every split pool far
# larger than the maximum draw.
CAPS = {"sciq": 150, "race": 200, "notes": 120}

# Static provenance for manifest.json (what each prepared dataset came from).
SOURCES = {
    "sciq": "HF allenai/sciq, split=test, passage>=250 chars",
    "race": "HF ehovy/race, configs middle+high, split=test, deduped, >=400 chars",
    "alcpl": "GitHub harrylclc/AL-CPL-dataset .preqs + Wikipedia REST summaries",
    "notes_corpus": (
        "HF ccdv/pubmed-summarization, config=document, split=validation, "
        "article>=3000/abstract>=200 chars"
    ),
    "duolingo": (
        "HF Zai04/Duolingo-Spaced-Repetition-Data learning_traces.13m.csv — "
        "first users' full histories, split by user"
    ),
    "ednet": (
        "HF mgor/EDNet kt1 shard 0 + questions — users with >=150 interactions "
        "over >=14 days, split by user"
    ),
}

# Every file each dataset prepares — manifest.json hashes exactly these.
DATASET_FILES = {
    "sciq": [f"sciq_{s}.jsonl" for s in SPLITS],
    "race": [f"race_{s}.jsonl" for s in SPLITS],
    "alcpl": ["alcpl.jsonl"],
    "notes_corpus": [f"notes_corpus_{s}.jsonl" for s in SPLITS],
    "duolingo": [f"duolingo_{s}.parquet" for s in SPLITS],
    "ednet": [f"ednet_{s}.parquet" for s in SPLITS] + ["ednet_questions.parquet"],
}


def _http_get(url: str) -> bytes:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read()


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"  wrote {path.name}: {len(rows)} cases ({path.stat().st_size:,} B)")


def _load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def _all_split_files_exist(name: str) -> bool:
    return all((DATA_DIR / f).exists() for f in DATASET_FILES[name])


def _write_split_jsonl(
    name: str, rows: list[dict], ratios: tuple[float, float, float] = (0.6, 0.2, 0.2)
) -> None:
    """Seeded shuffle → train/val/test JSONL files.

    Membership is deterministic (SAMPLE_SEED) for a given pool, so splits
    never silently reshuffle between preparations of the same revision.
    """
    import random

    shuffled = list(rows)
    random.Random(SAMPLE_SEED).shuffle(shuffled)
    n = len(shuffled)
    n_train = round(n * ratios[0])
    n_val = round(n * ratios[1])
    bounds = {
        "train": (0, n_train),
        "val": (n_train, n_train + n_val),
        "test": (n_train + n_val, n),
    }
    for split, (lo, hi) in bounds.items():
        _write_jsonl(DATA_DIR / f"{name}_{split}.jsonl", shuffled[lo:hi])


def _write_split_parquet_by_user(
    df,
    name: str,
    user_col: str,
    ratios: tuple[float, float, float],
    stratify: bool = False,
) -> None:
    """Split a parquet by WHOLE users (no learner spans a split).

    stratify=True deals users round-robin heaviest-first: a plain seeded
    shuffle can stack the heavy users into one split and starve another
    (Duolingo traces are heavy-tailed per user — a seed-42 shuffle gave the
    test split 1.2k scorable sessions vs val's 6k).
    """
    import random

    users = sorted(df[user_col].unique().tolist())
    n = len(users)
    if stratify:
        if len({round(r, 6) for r in ratios}) != 1:
            raise ValueError("stratified dealing needs equal ratios")
        rng = random.Random(SAMPLE_SEED)
        rng.shuffle(users)  # random tie-break before the stable heavy-first sort
        counts = df[user_col].value_counts()
        users.sort(key=lambda u: -int(counts.get(u, 0)))
        assignment = {u: SPLITS[i % len(SPLITS)] for i, u in enumerate(users)}
    else:
        random.Random(SAMPLE_SEED).shuffle(users)
        n_train = round(n * ratios[0])
        n_val = round(n * ratios[1])
        assignment = {}
        for i, u in enumerate(users):
            assignment[u] = (
                "train"
                if i < n_train
                else "val" if i < n_train + n_val
                else "test"
            )
    for split in SPLITS:
        chunk = [u for u, s in assignment.items() if s == split]
        part = df[df[user_col].isin(chunk)]
        out = DATA_DIR / f"{name}_{split}.parquet"
        part.to_parquet(out, index=False)
        print(
            f"  wrote {out.name}: {len(part):,} rows, {len(chunk)} users "
            f"({out.stat().st_size:,} B)"
        )


def _rebuild_manifest() -> None:
    """Hash + count every prepared file into manifest.json — the drift
    detector: a changed upstream revision or a corrupted file shows up as a
    sha256 mismatch without re-downloading anything."""
    def _file_entry(path: Path) -> dict:
        h = hashlib.sha256()
        with path.open("rb") as f:
            for block in iter(lambda: f.read(1 << 20), b""):
                h.update(block)
        entry = {"bytes": path.stat().st_size, "sha256": h.hexdigest()}
        if path.suffix == ".jsonl":
            entry["rows"] = sum(
                1 for line in path.open("rb") if line.strip()
            )
        else:
            import pyarrow.parquet as pq

            entry["rows"] = pq.read_metadata(path).num_rows
        return entry

    manifest: dict[str, dict] = {}
    for dataset, filenames in DATASET_FILES.items():
        if not (DATA_DIR / filenames[0]).exists():
            continue
        manifest[dataset] = {
            "source": SOURCES[dataset],
            "files": {fn: _file_entry(DATA_DIR / fn) for fn in filenames},
        }
    manifest["_prepared_at"] = datetime.now(timezone.utc).isoformat()
    out = DATA_DIR / "manifest.json"
    out.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    print(f"  wrote {out.name}: {len(manifest) - 1} datasets fingerprinted")


# ---------------------------------------------------------------------------
# SciQ — quiz-generation gold (passage + 1 correct answer + 3 distractors)
# ---------------------------------------------------------------------------


def prepare_sciq() -> None:
    if _all_split_files_exist("sciq"):
        print("sciq: already prepared")
        return
    print("sciq: downloading (allenai/sciq)…")
    from datasets import load_dataset

    rows = []
    for row in load_dataset("allenai/sciq", split="test"):
        passage = (row["support"] or "").strip()
        if len(passage) < 250:
            continue  # need real content for grounded generation
        rows.append(
            {
                "id": f"sciq-{len(rows)}",
                "passage": passage,
                "question": row["question"],
                "correct_answer": row["correct_answer"],
                "distractors": [
                    row["distractor1"],
                    row["distractor2"],
                    row["distractor3"],
                ],
            }
        )
        if len(rows) >= CAPS["sciq"]:
            break
    _write_split_jsonl("sciq", rows)


# ---------------------------------------------------------------------------
# RACE — reading-exam passages with difficulty tiers (middle/high school)
# ---------------------------------------------------------------------------


def prepare_race() -> None:
    if _all_split_files_exist("race"):
        print("race: already prepared")
        return
    print("race: downloading (ehovy/race, middle+high)…")
    from datasets import load_dataset

    seen: set[str] = set()
    rows = []
    for tier in ("middle", "high"):
        for row in load_dataset("ehovy/race", tier, split="test"):
            passage = row["article"].strip()
            if passage in seen or len(passage) < 400:
                continue
            seen.add(passage)
            rows.append(
                {
                    "id": f"race-{tier}-{len(rows)}",
                    "passage": passage,
                    "tier": tier,  # middle ≈ easier, high ≈ harder
                    "question": row["question"],
                    "options": row["options"],
                    "answer": row["answer"],
                }
            )
            if len(rows) >= CAPS["race"]:
                break
        if len(rows) >= CAPS["race"]:
            break
    _write_split_jsonl("race", rows)


# ---------------------------------------------------------------------------
# AL-CPL — gold concept lists + prerequisite pairs, composed into documents
# via Wikipedia summaries (the dataset ships labels, not lecture text)
# ---------------------------------------------------------------------------


def _wiki_summary(title: str) -> str | None:
    url = (
        "https://en.wikipedia.org/api/rest_v1/page/summary/"
        + urllib.request.quote(title.replace(" ", "_"))
    )
    try:
        data = json.loads(_http_get(url))
        return (data.get("extract") or "").strip()
    except Exception:
        return None


def prepare_alcpl(max_concepts_per_course: int = 12) -> None:
    out = DATA_DIR / "alcpl.jsonl"
    if out.exists():
        print("alcpl: already prepared")
        return
    print("alcpl: fetching labels + Wikipedia summaries…")
    rows = []
    for course in AL_CPL_COURSES:
        preqs = [
            line.strip().split(",")
            for line in _http_get(f"{AL_CPL_RAW}/data/{course}.preqs")
            .decode()
            .splitlines()
            if line.strip()
        ]
        # Greedily pick concepts that maximize gold prerequisite edges inside
        # the document (an alphabetical slice can end up edge-free).
        degree: dict[str, set[str]] = {}
        for a, b in preqs:
            degree.setdefault(a, set()).add(b)
            degree.setdefault(b, set()).add(a)
        picked: list[str] = []
        remaining = set(degree)
        if remaining:
            picked.append(max(remaining, key=lambda c: len(degree[c])))
            remaining.discard(picked[0])
        while remaining and len(picked) < max_concepts_per_course:
            best = max(
                remaining,
                key=lambda c: (
                    len(degree[c] & set(picked)),  # edges into the doc first
                    len(degree[c]),  # then overall connectivity
                ),
            )
            picked.append(best)
            remaining.discard(best)
        picked_set = set(picked)
        # Compose a "lecture notes" document from the picked concepts'
        # Wikipedia summaries — the doc genuinely contains these concepts,
        # so extraction has real gold to hit.
        sections = []
        for title in picked:
            summary = _wiki_summary(title)
            if summary and len(summary) > 120:
                # Strip the "(geometry)" style disambiguation for prose.
                clean = title.replace("_", " ").split(" (")[0]
                sections.append(f"## {clean}\n{summary}")
            time.sleep(0.05)  # be polite to the API
        if len(sections) < 4:
            print(f"  {course}: only {len(sections)} usable summaries, skipping")
            continue
        rows.append(
            {
                "id": f"alcpl-{course}",
                "course": course,
                "text": "\n\n".join(sections),
                "concepts": picked,
                # Gold prerequisite edges among the concepts actually in the doc.
                "prerequisite_pairs": [
                    [a, b] for a, b in preqs if a in picked_set and b in picked_set
                ],
            }
        )
    _write_jsonl(out, rows)


# ---------------------------------------------------------------------------
# Notes corpus — PubMed long documents with human-written abstracts
# ---------------------------------------------------------------------------


def prepare_notes() -> None:
    if _all_split_files_exist("notes_corpus"):
        print("notes: already prepared")
        return
    print("notes: downloading (ccdv/pubmed-summarization, document)…")
    from datasets import load_dataset

    rows = []
    for row in load_dataset(
        "ccdv/pubmed-summarization", "document", split="validation"
    ):
        article = " ".join(row["article"]).strip()
        abstract = " ".join(row["abstract"]).strip()
        if len(article) < 3000 or len(abstract) < 200:
            continue
        rows.append(
            {
                "id": f"pubmed-{len(rows)}",
                # Pre-truncated the way the production truncation would (70%
                # head / 30% tail at 12k chars) so gold summaries stay fair.
                "article": article[:8400] + article[-3600:],
                "summary": abstract,
            }
        )
        if len(rows) >= CAPS["notes"]:
            break
    _write_split_jsonl("notes_corpus", rows)


# ---------------------------------------------------------------------------
# Duolingo HLR — real spaced-repetition traces (settles.acl16)
# ---------------------------------------------------------------------------


def prepare_duolingo(max_users: int = 120) -> None:
    if _all_split_files_exist("duolingo"):
        print("duolingo: already prepared")
        return
    print("duolingo: downloading settles.acl16 traces (~500MB, one time)…")
    import pandas as pd
    from huggingface_hub import hf_hub_download

    csv_path = hf_hub_download(
        repo_id="Zai04/Duolingo-Spaced-Repetition-Data",
        filename="learning_traces.13m.csv",
        repo_type="dataset",
    )
    print("duolingo: sampling first users' full histories…")
    wanted: set[str] = set()
    frames = []
    for chunk in pd.read_csv(csv_path, chunksize=400_000):
        if not wanted:
            wanted = set(chunk["user_id"].unique()[:max_users])
        part = chunk[chunk["user_id"].isin(wanted)]
        if len(part):
            frames.append(part)
        if sum(len(f) for f in frames) > 400_000:
            break
    df = pd.concat(frames, ignore_index=True)
    # Equal thirds, stratified heaviest-first: the replay suites are cheap
    # (no LLM) and the fsrs suite needs a few thousand scorable sessions per
    # split — every split gets a balanced contingent of users.
    _write_split_parquet_by_user(
        df, "duolingo", "user_id", (1 / 3, 1 / 3, 1 / 3), stratify=True
    )


# ---------------------------------------------------------------------------
# EdNet-KT1 — real learner interaction logs (per-question correct/incorrect)
# ---------------------------------------------------------------------------


def prepare_ednet(n_users: int = 60) -> None:
    if _all_split_files_exist("ednet"):
        print("ednet: already prepared")
        return
    print("ednet: downloading one KT1 shard + questions (~200MB, one time)…")
    import pandas as pd
    import pyarrow.parquet as pq
    from huggingface_hub import hf_hub_download

    shard = hf_hub_download(
        repo_id="mgor/EDNet",
        filename="kt1/train-00000-of-00011.parquet",
        repo_type="dataset",
    )
    qfile = hf_hub_download(
        repo_id="mgor/EDNet",
        filename="questions/train-00000-of-00001.parquet",
        repo_type="dataset",
    )

    table = pq.read_table(
        shard,
        columns=["subject_id", "question_id", "timestamp", "is_correct", "elapsed_time"],
    )
    df = table.to_pandas()
    spans = df.groupby("subject_id")["timestamp"].agg(["min", "max", "count"])
    spans["days"] = (spans["max"] - spans["min"]) / 86_400_000
    # Enough history for several simulated decision points, spread over time.
    good = spans[(spans["count"] >= 150) & (spans["days"] >= 14)]
    picked = good.sort_values("days", ascending=False).head(n_users).index
    sample = df[df["subject_id"].isin(picked)].sort_values("timestamp")

    qdf = pq.read_table(
        qfile,
        columns=["question_id", "tags"],
    ).to_pandas()
    qdf = qdf[qdf["tags"].notna() & (qdf["tags"] != "")]
    out_questions = DATA_DIR / "ednet_questions.parquet"
    qdf.to_parquet(out_questions, index=False)
    print(f"  wrote {out_questions.name}: {len(qdf):,} tagged questions")
    # Equal thirds, stratified — the recommend replay caps at 20 users, so
    # every split can feed a full, comparable replay.
    _write_split_parquet_by_user(
        sample, "ednet", "subject_id", (1 / 3, 1 / 3, 1 / 3), stratify=True
    )


# ---------------------------------------------------------------------------


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    steps = {
        "sciq": prepare_sciq,
        "race": prepare_race,
        "alcpl": prepare_alcpl,
        "notes": prepare_notes,
        "duolingo": prepare_duolingo,
        "ednet": prepare_ednet,
    }
    only = sys.argv[1:] or list(steps)
    for name in only:
        if name not in steps:
            raise SystemExit(f"unknown dataset {name!r}; options: {list(steps)}")
        try:
            steps[name]()
        except Exception as exc:  # keep preparing the rest; report at the end
            print(f"  !! {name} failed: {exc}", file=sys.stderr)
    _rebuild_manifest()
    print("done.")


if __name__ == "__main__":
    main()
