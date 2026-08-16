"""Download + prepare the public datasets behind the eval suites.

    uv run python -m evals.data            # prepare everything (idempotent)

Everything lands in evals/data/ (gitignored) as small, normalized JSONL /
parquet samples so the suites never touch the network. Large upstream files
(Duolingo 13M traces, EdNet shards) are pulled into the Hugging Face cache,
never into the repo — only the sampled slices are written here.

Datasets (all public, research use; see evals/README.md for licenses):
  - SciQ            HF allenai/sciq        — passages + gold MCQs/distractors
  - RACE            HF ehovy/race          — passages + difficulty tiers
  - AL-CPL          GitHub harrylclc/AL-CPL-dataset + Wikipedia summaries
                                          — gold concepts + prerequisite pairs
  - PubMed (notes)  HF ccdv/pubmed-summarization — long docs + human abstracts
  - Duolingo HLR    HF Zai04/Duolingo-Spaced-Repetition-Data (settles.acl16)
                                          — real forgetting-curve traces
  - EdNet-KT1       HF mgor/EDNet          — real learner interaction logs
"""

from __future__ import annotations

import json
import sys
import time
import urllib.request
from pathlib import Path

from evals.config import DATA_DIR

UA = {"User-Agent": "study-app-evals/0.1 (research; contact: dev)"}

AL_CPL_COURSES = ["data_mining", "geometry", "physics", "precalculus"]
AL_CPL_RAW = (
    "https://raw.githubusercontent.com/harrylclc/AL-CPL-dataset/master"
)


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


# ---------------------------------------------------------------------------
# SciQ — quiz-generation gold (passage + 1 correct answer + 3 distractors)
# ---------------------------------------------------------------------------


def prepare_sciq() -> None:
    out = DATA_DIR / "sciq.jsonl"
    if out.exists():
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
    _write_jsonl(out, rows)


# ---------------------------------------------------------------------------
# RACE — reading-exam passages with difficulty tiers (middle/high school)
# ---------------------------------------------------------------------------


def prepare_race() -> None:
    out = DATA_DIR / "race.jsonl"
    if out.exists():
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
    _write_jsonl(out, rows)


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
    out = DATA_DIR / "notes_corpus.jsonl"
    if out.exists():
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
        if len(rows) >= 400:
            break
    _write_jsonl(out, rows)


# ---------------------------------------------------------------------------
# Duolingo HLR — real spaced-repetition traces (settles.acl16)
# ---------------------------------------------------------------------------


def prepare_duolingo(max_users: int = 250) -> None:
    out = DATA_DIR / "duolingo_sample.parquet"
    if out.exists():
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
    df.to_parquet(out, index=False)
    users = df["user_id"].nunique()
    print(
        f"  wrote {out.name}: {len(df):,} rows, {users} users "
        f"({out.stat().st_size:,} B)"
    )


# ---------------------------------------------------------------------------
# EdNet-KT1 — real learner interaction logs (per-question correct/incorrect)
# ---------------------------------------------------------------------------


def prepare_ednet(n_users: int = 40) -> None:
    out_users = DATA_DIR / "ednet_sample.parquet"
    out_questions = DATA_DIR / "ednet_questions.parquet"
    if out_users.exists() and out_questions.exists():
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
    sample.to_parquet(out_users, index=False)

    qdf = pq.read_table(
        qfile,
        columns=["question_id", "tags"],
    ).to_pandas()
    qdf = qdf[qdf["tags"].notna() & (qdf["tags"] != "")]
    qdf.to_parquet(out_questions, index=False)
    print(
        f"  wrote {out_users.name}: {len(sample):,} rows, "
        f"{sample['subject_id'].nunique()} users; "
        f"{out_questions.name}: {len(qdf):,} tagged questions"
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
    print("done.")


if __name__ == "__main__":
    main()
